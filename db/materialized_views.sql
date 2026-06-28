-- =============================================================================
-- FinGuard Analytics — Materialized Views
-- =============================================================================
-- These three views are the ENTIRE point of the "Postgres-heavy" architecture
-- decision: the interactive frontend queries these, never fact_transactions
-- directly. Each view backs exactly one FastAPI router (see mapping below),
-- so there is a 1:1 traceable line from "what the user clicks" to "which
-- view answers it" to "which router serves it" — worth stating explicitly
-- here since it's the project's main interview talking point.
--
--   mv_hourly_fraud_summary   -> backend/routers/overview.py
--   mv_segment_fraud_rates    -> backend/routers/fraud_patterns.py
--   mv_account_velocity       -> backend/routers/velocity.py
--
-- REFRESH STRATEGY
-- -----------------
-- All three views are created WITH DATA (eagerly populated at creation
-- time) and are refreshed via REFRESH MATERIALIZED VIEW CONCURRENTLY in
-- refresh_views.py. CONCURRENTLY requires a UNIQUE index on the view
-- (added below for each) and does not block concurrent reads during
-- refresh — the correct choice here since refresh_views.py is expected to
-- run on a schedule (e.g. via a GitHub Actions cron, or manually post-load)
-- while the FastAPI app may be serving live traffic. The trade-off is that
-- CONCURRENTLY takes somewhat longer than a plain REFRESH and requires
-- enough free disk to hold both the old and new view data simultaneously
-- during the swap — acceptable here given each view's footprint is small
-- relative to the dataset (each is an aggregate, not a copy of fact rows).
--
-- DATA NOTE: PaySim fraud (isFraud = true) occurs only within TRANSFER and
-- CASH_OUT transaction types by construction of the source dataset. The
-- views below do not hardcode this — they aggregate across all 5 types
-- and let the data speak for itself — but it's worth knowing going in so
-- a near-zero fraud rate for PAYMENT/CASH_IN/DEBIT rows in query results
-- is expected, not a bug.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- mv_hourly_fraud_summary
-- -----------------------------------------------------------------------------
-- Grain: one row per `step` (PaySim's hourly time unit, 1-743).
-- Backs: GET /overview/* — the dashboard landing page's KPI strip and
-- time-series chart (transaction volume / fraud rate over time).
--
-- Without this view, rendering an hourly fraud-rate chart would require
-- GROUP BY step over all 858K fact rows on every page load — cheap at this
-- row count today, but the entire point of this architecture is that it
-- should not need to scale with fact table size as more data is added.
CREATE MATERIALIZED VIEW mv_hourly_fraud_summary AS
SELECT
    f.step,

    COUNT(*)                                              AS total_transactions,
    COUNT(*) FILTER (WHERE f.is_fraud)                    AS fraud_transactions,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE f.is_fraud) / GREATEST(COUNT(*), 1),
        4
    )                                                      AS fraud_rate_pct,

    SUM(f.amount)                                          AS total_amount,
    SUM(f.amount) FILTER (WHERE f.is_fraud)                AS fraud_amount,
    ROUND(AVG(f.amount), 2)                                AS avg_amount,

    COUNT(*) FILTER (WHERE f.is_flagged_fraud)             AS flagged_fraud_count,
    -- Flagged-but-not-actually-fraud and fraud-but-not-flagged are both
    -- analytically interesting (PaySim's flagging rule has known low
    -- recall) — surfaced here so fraud_patterns.py / findings can quantify
    -- detection-rule performance without re-deriving it from raw rows.
    COUNT(*) FILTER (WHERE f.is_flagged_fraud AND NOT f.is_fraud)  AS false_positive_flags,
    COUNT(*) FILTER (WHERE f.is_fraud AND NOT f.is_flagged_fraud) AS missed_fraud_count

FROM fact_transactions f
GROUP BY f.step
WITH DATA;

COMMENT ON MATERIALIZED VIEW mv_hourly_fraud_summary IS
    'Hourly (per-step) transaction and fraud aggregates. Backs the overview '
    'dashboard KPI strip and time-series chart. One row per distinct step '
    'value present in fact_transactions.';

-- UNIQUE index required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
CREATE UNIQUE INDEX idx_mv_hourly_fraud_summary_step
    ON mv_hourly_fraud_summary (step);


-- -----------------------------------------------------------------------------
-- mv_segment_fraud_rates
-- -----------------------------------------------------------------------------
-- Grain: one row per (transaction_type, amount_bucket).
-- Backs: GET /fraud-patterns/* — the fraud-pattern explorer's segment
-- filter/heatmap (e.g. "show me fraud rate by type and amount range").
--
-- Amount buckets are fixed, named ranges rather than a continuous
-- histogram — chosen because the frontend's filter UI needs discrete,
-- stable bucket labels to render as filter chips/heatmap cells, and
-- because PaySim amounts span several orders of magnitude (single-digit
-- to ~92M), so equal-width buckets would be useless (almost everything
-- falls in the lowest bucket). Bucket boundaries below are intentionally
-- log-scale-ish, chosen to give roughly even data density across buckets
-- rather than even dollar-width — verify against findings_report.md once
-- the project's analysis phase runs real numbers; these are reasonable
-- starting boundaries, not output of a rigorous quantile analysis.
CREATE MATERIALIZED VIEW mv_segment_fraud_rates AS
SELECT
    tt.type_name,
    CASE
        WHEN f.amount < 1000        THEN '0-1K'
        WHEN f.amount < 10000       THEN '1K-10K'
        WHEN f.amount < 100000      THEN '10K-100K'
        WHEN f.amount < 1000000     THEN '100K-1M'
        ELSE '1M+'
    END                                                     AS amount_bucket,
    -- Sort key so the API/frontend can ORDER BY this instead of relying on
    -- string-sorting the bucket labels (which would sort "100K-1M" before
    -- "1K-10K" lexicographically — wrong order for a numeric range).
    CASE
        WHEN f.amount < 1000        THEN 1
        WHEN f.amount < 10000       THEN 2
        WHEN f.amount < 100000      THEN 3
        WHEN f.amount < 1000000     THEN 4
        ELSE 5
    END                                                     AS amount_bucket_sort_order,

    COUNT(*)                                                AS total_transactions,
    COUNT(*) FILTER (WHERE f.is_fraud)                      AS fraud_transactions,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE f.is_fraud) / GREATEST(COUNT(*), 1),
        4
    )                                                        AS fraud_rate_pct,

    SUM(f.amount)                                            AS total_amount,
    SUM(f.amount) FILTER (WHERE f.is_fraud)                  AS fraud_amount,
    ROUND(AVG(f.amount), 2)                                  AS avg_amount

FROM fact_transactions f
JOIN dim_transaction_type tt
    ON f.transaction_type_key = tt.transaction_type_key
GROUP BY
    tt.type_name,
    CASE
        WHEN f.amount < 1000        THEN '0-1K'
        WHEN f.amount < 10000       THEN '1K-10K'
        WHEN f.amount < 100000      THEN '10K-100K'
        WHEN f.amount < 1000000     THEN '100K-1M'
        ELSE '1M+'
    END,
    CASE
        WHEN f.amount < 1000        THEN 1
        WHEN f.amount < 10000       THEN 2
        WHEN f.amount < 100000      THEN 3
        WHEN f.amount < 1000000     THEN 4
        ELSE 5
    END
WITH DATA;

COMMENT ON MATERIALIZED VIEW mv_segment_fraud_rates IS
    'Fraud rate and volume aggregates by transaction type and amount '
    'bucket. Backs the fraud-pattern explorer segment filter/heatmap. '
    'One row per (type_name, amount_bucket) combination present in data.';

-- Composite unique index required for CONCURRENTLY refresh; also serves
-- as the natural lookup index for "filter by type AND bucket" queries.
CREATE UNIQUE INDEX idx_mv_segment_fraud_rates_type_bucket
    ON mv_segment_fraud_rates (type_name, amount_bucket);


-- -----------------------------------------------------------------------------
-- mv_account_velocity
-- -----------------------------------------------------------------------------
-- Grain: one row per account (account_key), covering activity where the
-- account appears as ORIGIN. Backs: GET /velocity/{account_id} — the
-- per-account drill-down endpoint.
--
-- Deliberately origin-only (not also aggregating destination-side
-- activity into the same row) because "velocity" in a fraud context
-- specifically means "how fast is money leaving this account" — outbound
-- transaction frequency/volume is the standard mule-account signal.
-- Destination-side inbound aggregates would answer a different question
-- ("is this account receiving from many sources") and are intentionally
-- left for a future view rather than conflated into this one's columns.
CREATE MATERIALIZED VIEW mv_account_velocity AS
SELECT
    a.account_key,
    a.account_id,
    a.account_type,

    COUNT(*)                                                AS total_transactions,
    COUNT(*) FILTER (WHERE f.is_fraud)                      AS fraud_transactions,
    COUNT(DISTINCT f.dest_account_key)                       AS distinct_counterparties,

    SUM(f.amount)                                            AS total_amount_sent,
    ROUND(AVG(f.amount), 2)                                  AS avg_amount_sent,
    MAX(f.amount)                                             AS max_amount_sent,

    MIN(f.step)                                               AS first_step,
    MAX(f.step)                                               AS last_step,
    -- Span in hours between this account's first and last observed
    -- transaction — the core "velocity" denominator (transactions per
    -- unit time). 0 when an account has exactly one transaction; the API
    -- layer is responsible for deciding how to render a rate against a
    -- zero-width span (e.g. "N/A" rather than dividing by zero).
    (MAX(f.step) - MIN(f.step))                              AS active_step_span,

    ROUND(
        COUNT(*)::NUMERIC / GREATEST(MAX(f.step) - MIN(f.step), 1),
        4
    )                                                          AS avg_transactions_per_step

FROM dim_account a
JOIN fact_transactions f
    ON f.orig_account_key = a.account_key
GROUP BY a.account_key, a.account_id, a.account_type
WITH DATA;

COMMENT ON MATERIALIZED VIEW mv_account_velocity IS
    'Per-account outbound transaction velocity: count, volume, distinct '
    'counterparties, and time-span, computed over transactions where the '
    'account is the ORIGIN. Backs the velocity drill-down endpoint. '
    'Only includes accounts with at least one outbound transaction — '
    'accounts seen only as a destination (e.g. some merchants) do not '
    'appear here by design.';

CREATE UNIQUE INDEX idx_mv_account_velocity_account_key
    ON mv_account_velocity (account_key);

-- Supports "show me the highest-velocity accounts" style queries
-- (a natural fraud_patterns.py / overview.py cross-cutting query) without
-- a full scan of this already-small materialized view.
CREATE INDEX idx_mv_account_velocity_fraud_count
    ON mv_account_velocity (fraud_transactions DESC)
    WHERE fraud_transactions > 0;


-- -----------------------------------------------------------------------------
-- Notes for refresh_views.py
-- -----------------------------------------------------------------------------
-- All three views must be refreshed in this order after any change to
-- fact_transactions / dim_account (e.g. a re-load):
--
--   REFRESH MATERIALIZED VIEW CONCURRENTLY mv_hourly_fraud_summary;
--   REFRESH MATERIALIZED VIEW CONCURRENTLY mv_segment_fraud_rates;
--   REFRESH MATERIALIZED VIEW CONCURRENTLY mv_account_velocity;
--
-- Order does not matter for correctness (the three views are independent
-- of each other, only dependent on the base tables), but mv_account_velocity
-- is the most expensive to refresh (joins the full fact table against
-- dim_account) — running it last means a failure there doesn't block the
-- cheaper two views from being up to date.
--
-- CONCURRENTLY requires each view to already exist with its unique index
-- (created above) — this is why schema.sql and materialized_views.sql
-- must both be applied via this file before load_data.py's verification
-- step, and why refresh_views.py should never attempt to DROP and
-- recreate these views as part of a routine refresh (only this .sql file
-- should do that, and only when the view DEFINITION changes, not the data).