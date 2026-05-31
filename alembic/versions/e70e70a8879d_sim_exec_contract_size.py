"""iter-sim-exec-cs-precision: sessions.contract_size nullable column

Per-session market contractSize, cached at SimulatedExchange.start().
Legacy sessions keep NULL → analysis layer falls back to cs=1.0 (old base
semantics), new runs store real cs (contracts semantics). Mirrors the
existing nullable sessions.fee_rate column.

Revision ID: e70e70a8879d
Revises: af87432ee6dd
Create Date: 2026-05-31 13:33:03.775983
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e70e70a8879d"
down_revision: str | None = "af87432ee6dd"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("contract_size", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("contract_size")
