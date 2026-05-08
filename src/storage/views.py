"""Phase 1 observability view SQL — single source of truth.

Imported by both:
1. `alembic/versions/61ac4841a55d_phase1_observability.py` upgrade()
   — applies views as part of migration chain.
2. `src/storage/database.py` init_db Path 3 (fresh empty DB → create_all + stamp head)
   — applies views directly because Path 3 skips migration upgrade().

Path 3 用 stamp 不 run migration，所以 view 在 fresh init_db 后会缺失（ship 路径
失败而非 fixture-only 问题，PR #42 review 揭示）。本模块作 single source 让
两条路径行为一致。
"""
from __future__ import annotations

V_CYCLE_METRICS_SQL = """
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


V_ALERT_LIFECYCLE_SQL = """
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


V_ORDER_LIFECYCLE_SQL = """
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


ALL_VIEW_SQLS: tuple[str, ...] = (
    V_CYCLE_METRICS_SQL,
    V_ALERT_LIFECYCLE_SQL,
    V_ORDER_LIFECYCLE_SQL,
)


ALL_VIEW_NAMES: tuple[str, ...] = (
    "v_cycle_metrics",
    "v_alert_lifecycle",
    "v_order_lifecycle",
)
