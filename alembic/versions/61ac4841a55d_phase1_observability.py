"""phase1 observability

Revision ID: 61ac4841a55d
Revises: eeeee565cb36
Create Date: 2026-05-08 15:57:49.004742

Phase 1 spec §5.1.3: agent_cycles 加 8 列 (timing 2 + tokens 6) +
trade_actions 加 alert_id 列 + 3 read-only views (v_cycle_metrics /
v_alert_lifecycle / v_order_lifecycle).
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "61ac4841a55d"
down_revision: str | None = "eeeee565cb36"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# View SQL 字符串（T13/T15/T17 后续 task 填充；本 task 仅占位空字符串）
_V_CYCLE_METRICS_SQL = ""      # T13 填充
_V_ALERT_LIFECYCLE_SQL = ""    # T15 填充
_V_ORDER_LIFECYCLE_SQL = ""    # T17 填充


def upgrade() -> None:
    # P1+P2: agent_cycles 加 8 列（全 nullable）
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("wall_time_ms",       sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("llm_call_ms",        sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("input_tokens",       sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("output_tokens",      sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_read_tokens",  sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_write_tokens", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("reasoning_tokens",   sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_hit_rate",     sa.Float,   nullable=True))

    # X 配套: trade_actions 加 alert_id（nullable）
    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("alert_id", sa.String(50), nullable=True))

    # P5+P6: 创建 3 个 view（占位 — 后续 task 填充 SQL 字符串）
    if _V_CYCLE_METRICS_SQL:
        op.execute(_V_CYCLE_METRICS_SQL)
    if _V_ALERT_LIFECYCLE_SQL:
        op.execute(_V_ALERT_LIFECYCLE_SQL)
    if _V_ORDER_LIFECYCLE_SQL:
        op.execute(_V_ORDER_LIFECYCLE_SQL)


def downgrade() -> None:
    # Drop views first (column dependency)
    op.execute("DROP VIEW IF EXISTS v_order_lifecycle")
    op.execute("DROP VIEW IF EXISTS v_alert_lifecycle")
    op.execute("DROP VIEW IF EXISTS v_cycle_metrics")

    # Drop trade_actions.alert_id
    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.drop_column("alert_id")

    # Drop agent_cycles 8 列（按 add 顺序的反向）
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        for col in ("cache_hit_rate", "reasoning_tokens", "cache_write_tokens",
                    "cache_read_tokens", "output_tokens", "input_tokens",
                    "llm_call_ms", "wall_time_ms"):
            batch_op.drop_column(col)
