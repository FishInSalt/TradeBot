"""iter-tool-opt-net-pnl-metrics: trade_actions amount + entry_price + view DDL update

Revision ID: af87432ee6dd
Revises: 4ee6c95d0430
Create Date: 2026-05-16 10:36:05.182872

Adds two nullable columns to trade_actions (per spec §C0/§C1) and rebuilds
v_cycle_metrics view to read $.position.pnl_pct_of_notional (per spec §C0/§C6).

Legacy rows (pre-migration) keep entry_price/amount as NULL by design;
their v_cycle_metrics.position_pnl_pct column returns NULL after this
migration (per spec §6.11; not backfilled).
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.storage.views import (
    V_CYCLE_METRICS_SQL,
    V_ALERT_LIFECYCLE_SQL,
    V_ORDER_LIFECYCLE_SQL,
    ALL_VIEW_NAMES,
)


revision: str = "af87432ee6dd"
down_revision: str | None = "4ee6c95d0430"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# Pre-iter V_CYCLE_METRICS_SQL (copy verbatim from `git show e7f7e78:src/storage/views.py`).
# Used by downgrade() to recreate the view exactly as it was before this migration.
# WARNING: maintain in sync with that historical version; do NOT use abbreviated SQL.
_V_CYCLE_METRICS_SQL_PRE_ITER = """
-- CAVEAT (PR #42 review v5 I-4): 5-field anchor 用 substring LIKE，未 anchor 到行首.
-- R2-Next-A priors-injection 会把上 cycle decision 灌入 prompt; agent 引述时
-- "(1) Stance:" 会出现在 reasoning / 注释里触发 false positive。AC-9 drift-guard
-- 看聚合命中率高低看不出引述污染。Phase 2 / R2-Next-J 收紧到行首正则
-- (e.g. ^\\s*(\\*\\*)?\\(1\\)) 是 follow-up.
CREATE VIEW v_cycle_metrics AS
WITH ac_with_anchors AS (
  SELECT
    ac.*,
    CASE WHEN ac.decision LIKE '%(1) Stance%' OR ac.decision LIKE '%(1) **Stance%'
           OR ac.decision LIKE '%**(1) Stance%' OR ac.decision LIKE '%**(1)** Stance%'
         THEN 1 ELSE 0 END AS has_stance,
    CASE WHEN ac.decision LIKE '%(2) Active%' OR ac.decision LIKE '%(2) **Active%'
           OR ac.decision LIKE '%**(2) Active%' OR ac.decision LIKE '%**(2)** Active%'
         THEN 1 ELSE 0 END AS has_active_commitments,
    CASE WHEN ac.decision LIKE '%(3) This cycle%' OR ac.decision LIKE '%(3) **This cycle%'
           OR ac.decision LIKE '%**(3) This cycle%' OR ac.decision LIKE '%**(3)** This cycle%'
         THEN 1 ELSE 0 END AS has_this_cycle_delta,
    CASE WHEN ac.decision LIKE '%(4) Thesis%' OR ac.decision LIKE '%(4) **Thesis%'
           OR ac.decision LIKE '%**(4) Thesis%' OR ac.decision LIKE '%**(4)** Thesis%'
         THEN 1 ELSE 0 END AS has_thesis_invalidation,
    CASE WHEN ac.decision LIKE '%(5) Watch%' OR ac.decision LIKE '%(5) **Watch%'
           OR ac.decision LIKE '%**(5) Watch%' OR ac.decision LIKE '%**(5)** Watch%'
         THEN 1 ELSE 0 END AS has_watch_list
  FROM agent_cycles ac
)
SELECT
  ac.session_id, ac.cycle_id, ac.triggered_by, ac.execution_status,
  ac.created_at, ac.model_id,
  ac.wall_time_ms, ac.llm_call_ms,
  COALESCE(
    (SELECT SUM(tc.duration_ms) FROM tool_calls tc
     WHERE tc.session_id=ac.session_id AND tc.cycle_id=ac.cycle_id),
    0
  ) AS tool_total_ms,
  ac.tokens_consumed, ac.input_tokens, ac.output_tokens,
  ac.cache_read_tokens, ac.cache_write_tokens,
  ac.reasoning_tokens,
  ac.cache_hit_rate,
  CASE WHEN ac.input_tokens IS NOT NULL AND ac.input_tokens > 0
       THEN ac.cache_read_tokens * 100.0 / ac.input_tokens
       ELSE NULL END AS cache_hit_rate_derived,
  CAST(json_extract(ac.state_snapshot, '$.position.contracts')      AS REAL)    AS position_size,
       json_extract(ac.state_snapshot, '$.position.side')                       AS position_side,
  CAST(json_extract(ac.state_snapshot, '$.position.leverage')       AS INTEGER) AS position_leverage,
  CAST(json_extract(ac.state_snapshot, '$.position.unrealized_pnl') AS REAL)    AS position_unrealized_pnl,
  CAST(json_extract(ac.state_snapshot, '$.position.pnl_pct')        AS REAL)    AS position_pnl_pct,
  CAST(json_extract(ac.state_snapshot, '$.balance.free_usdt')       AS REAL)    AS balance_free_usdt,
  CAST(json_extract(ac.state_snapshot, '$.market.ticker_last')      AS REAL)    AS ticker_last,
       json_extract(ac.state_snapshot, '$.market.fetched_at')                   AS state_captured_at,
  json_array_length(json_extract(ac.state_snapshot, '$.pending_orders')) AS pending_orders_count,
  json_array_length(json_extract(ac.state_snapshot, '$.active_alerts'))  AS active_alerts_count,
  json_array_length(json_extract(ac.state_snapshot, '$._errors'))        AS snapshot_errors_count,
  CASE WHEN json_extract(ac.state_snapshot, '$.position') IS NOT NULL
       THEN 1 ELSE 0 END AS has_position,
  length(ac.decision) AS decision_length,
  ac.has_stance, ac.has_active_commitments, ac.has_this_cycle_delta,
  ac.has_thesis_invalidation, ac.has_watch_list,
  CASE WHEN (ac.has_stance + ac.has_active_commitments
           + ac.has_this_cycle_delta + ac.has_thesis_invalidation) >= 4
       THEN 1 ELSE 0 END AS five_field_complete,
  CASE WHEN ac.execution_status='ok'
        AND ac.decision IS NOT NULL
        AND length(ac.decision) > 0
       THEN 1 ELSE 0 END AS is_ok_cycle,
  CASE WHEN ac.execution_status IN ('retry_exhausted','usage_limit_exceeded')
       THEN 1 ELSE 0 END AS is_forensic_cycle
FROM ac_with_anchors ac
"""


def upgrade() -> None:
    # SQLite's batch_alter_table (temp-table rename) fails when ANY view references
    # the target table. trade_actions is referenced by v_alert_lifecycle (registers/
    # cancels CTEs) and v_order_lifecycle (originated_cycle_id subquery) in addition
    # to v_cycle_metrics. Drop all 3, alter, recreate all 3.
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")

    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("entry_price", sa.Float(), nullable=True))

    # Recreate views — v_cycle_metrics now reads $.position.pnl_pct_of_notional.
    op.execute(V_CYCLE_METRICS_SQL)
    op.execute(V_ALERT_LIFECYCLE_SQL)
    op.execute(V_ORDER_LIFECYCLE_SQL)


def downgrade() -> None:
    # Same view-coupling concern as upgrade(): drop all 3 before batch alter.
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")

    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.drop_column("entry_price")
        batch_op.drop_column("amount")

    # Restore views — v_cycle_metrics uses pre-iter SQL (reads $.position.pnl_pct).
    op.execute(_V_CYCLE_METRICS_SQL_PRE_ITER)
    op.execute(V_ALERT_LIFECYCLE_SQL)
    op.execute(V_ORDER_LIFECYCLE_SQL)
