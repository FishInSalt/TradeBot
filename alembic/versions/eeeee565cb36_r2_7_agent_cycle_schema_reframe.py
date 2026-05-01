"""r2_7 agent_cycle schema reframe

Revision ID: eeeee565cb36
Revises: e7b2bd73c131
Create Date: 2026-05-01 19:46:45.670715

R2-7 spec §7.1: rename table + 5 columns + decision Text/nullable + state_snapshot.
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "eeeee565cb36"
down_revision: str | None = "e7b2bd73c131"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: rename table
    op.rename_table("decision_logs", "agent_cycles")

    # Step 2: batch_alter (SQLite limit — multi-column ALTER 必须 batch)
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.alter_column("trigger_type", new_column_name="triggered_by")
        batch_op.alter_column("market_summary", new_column_name="trigger_context")
        batch_op.alter_column("status", new_column_name="execution_status")
        batch_op.alter_column("model_used", new_column_name="model_id")
        batch_op.alter_column("tokens_used", new_column_name="tokens_consumed")

        batch_op.alter_column(
            "decision",
            existing_type=sa.String(30),
            type_=sa.Text(),
            existing_nullable=False,
            nullable=True,
        )

        batch_op.add_column(sa.Column("state_snapshot", sa.Text(), nullable=True))

    # Step 3: rename index (SQLite drop + recreate)
    op.drop_index("ix_decision_logs_session_id_cycle_id", table_name="agent_cycles")
    op.create_index(
        "ix_agent_cycles_session_id_cycle_id",
        "agent_cycles",
        ["session_id", "cycle_id"],
    )


def downgrade() -> None:
    # Escape hatch: 若 R2-7 后有 forensic NULL 行, 先手动清理:
    #   DELETE FROM agent_cycles WHERE execution_status='usage_limit_exceeded' AND decision IS NULL;
    # 否则 batch_alter 把 decision 收紧回 NOT NULL 时会爆 IntegrityError.
    # Step 1: drop new index (table 此时仍叫 agent_cycles)
    op.drop_index("ix_agent_cycles_session_id_cycle_id", table_name="agent_cycles")

    # Step 2: batch_alter — 列名/类型/nullable 全部回滚
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.drop_column("state_snapshot")
        batch_op.alter_column(
            "decision",
            existing_type=sa.Text(),
            type_=sa.String(30),
            existing_nullable=True,
            nullable=False,
        )
        batch_op.alter_column("tokens_consumed", new_column_name="tokens_used")
        batch_op.alter_column("model_id", new_column_name="model_used")
        batch_op.alter_column("execution_status", new_column_name="status")
        batch_op.alter_column("trigger_context", new_column_name="market_summary")
        batch_op.alter_column("triggered_by", new_column_name="trigger_type")

    # Step 3: rename table 回原名
    op.rename_table("agent_cycles", "decision_logs")

    # Step 4: 在已 rename 的表上重建旧 index
    op.create_index(
        "ix_decision_logs_session_id_cycle_id",
        "decision_logs",
        ["session_id", "cycle_id"],
    )
