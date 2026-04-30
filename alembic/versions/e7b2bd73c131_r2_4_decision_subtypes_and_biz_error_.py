"""r2_4 decision subtypes and biz error metrics

Revision ID: e7b2bd73c131
Revises: 379f62306805
Create Date: 2026-04-30 20:47:14.187715

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md

Schema-only migration. No data backfill — historical 'adjust' rows
preserved verbatim per A-strategy decision (see spec §5.5).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b2bd73c131'
down_revision: Union[str, Sequence[str], None] = '379f62306805'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P0-1: tool_calls.status 容量扩容（与 decision_logs.status 是不同列，本 R2-4 不动后者）
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=10),
            type_=sa.String(length=20),
            existing_nullable=False,
        )

    # P0-3: decision_logs.decision 容量扩容（与 decision_logs.status 不同列）
    with op.batch_alter_table("decision_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "decision",
            existing_type=sa.String(length=20),
            type_=sa.String(length=30),
            existing_nullable=False,
        )


def downgrade() -> None:
    # 反向：仅给开发期 rollback；生产 W2 不做 downgrade（spec §6.4 风险表）
    # CAVEAT: 若 DB 中已有新 enum 值（如 'adjust_entry_order' 18 char）
    # SQLite batch_alter_table 模式下 String 长度收紧不强制截断；PostgreSQL 会拒绝。
    with op.batch_alter_table("decision_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "decision",
            existing_type=sa.String(length=30),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
