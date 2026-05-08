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
_V_CYCLE_METRICS_SQL = """
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
  (SELECT SUM(tc.duration_ms) FROM tool_calls tc
   WHERE tc.session_id=ac.session_id AND tc.cycle_id=ac.cycle_id) AS tool_total_ms,
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
_V_ALERT_LIFECYCLE_SQL = """
CREATE VIEW v_alert_lifecycle AS
WITH registers AS (
  SELECT session_id, alert_id,
         created_at AS registered_at,
         price AS target_price,
         reasoning AS register_reasoning
  FROM trade_actions
  WHERE action='add_price_level_alert' AND alert_id IS NOT NULL
),
triggers AS (
  SELECT session_id,
         json_extract(trigger_context, '$.alert_id') AS alert_id,
         created_at AS triggered_at,
         CAST(json_extract(trigger_context, '$.current_price') AS REAL) AS triggered_price
  FROM agent_cycles
  WHERE triggered_by='alert'
    AND json_extract(trigger_context, '$.type')='price_level_alert'
    AND json_extract(trigger_context, '$.alert_id') IS NOT NULL
),
cancels AS (
  SELECT session_id, alert_id,
         created_at AS cancelled_at,
         reasoning AS cancel_reasoning
  FROM trade_actions
  WHERE action='cancel_price_level_alert' AND alert_id IS NOT NULL
),
cancel_attempts AS (
  SELECT session_id,
         json_extract(args, '$.alert_id') AS alert_id,
         COUNT(*) AS attempt_count,
         SUM(CASE WHEN status='biz_error' THEN 1 ELSE 0 END) AS attempt_failures
  FROM tool_calls
  WHERE tool_name='cancel_price_level_alert'
  GROUP BY session_id, json_extract(args, '$.alert_id')
)
SELECT
  r.session_id,
  r.alert_id,
  r.registered_at,
  r.target_price,
  r.register_reasoning,
  t.triggered_at,
  t.triggered_price,
  c.cancelled_at,
  c.cancel_reasoning,
  COALESCE(ca.attempt_count, 0)    AS cancel_attempt_count,
  COALESCE(ca.attempt_failures, 0) AS cancel_attempt_failures,
  CASE
    WHEN t.triggered_at IS NOT NULL THEN 'triggered'
    WHEN c.cancelled_at IS NOT NULL THEN 'cancelled'
    ELSE 'active'
  END AS final_status
FROM registers r
LEFT JOIN triggers       t  ON t.session_id=r.session_id  AND t.alert_id=r.alert_id
LEFT JOIN cancels        c  ON c.session_id=r.session_id  AND c.alert_id=r.alert_id
LEFT JOIN cancel_attempts ca ON ca.session_id=r.session_id AND ca.alert_id=r.alert_id
"""
_V_ORDER_LIFECYCLE_SQL = """
CREATE VIEW v_order_lifecycle AS
SELECT
  so.session_id,
  so.order_id, so.symbol, so.side, so.position_side,
  so.order_type, so.amount,
  so.trigger_price, so.filled_price, so.fee, so.leverage, so.frozen_margin,
  so.created_at, so.filled_at, so.status,
  CASE
    WHEN so.filled_at IS NOT NULL
    THEN CAST(ROUND((julianday(so.filled_at) - julianday(so.created_at)) * 86400) AS INTEGER)
  END AS lifetime_seconds,
  CASE
    WHEN so.order_type IN ('stop','take_profit')
     AND so.trigger_price IS NOT NULL AND so.filled_price IS NOT NULL
    THEN (so.filled_price - so.trigger_price) / so.trigger_price * 100.0
    ELSE NULL
  END AS trigger_drift_pct,
  (SELECT ta.cycle_id
   FROM trade_actions ta
   WHERE ta.order_id=so.order_id
     AND ta.action IN ('open_position','close_position','place_limit_order',
                       'set_stop_loss','set_take_profit')
   ORDER BY ta.created_at LIMIT 1) AS originated_cycle_id
FROM sim_orders so
"""


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
