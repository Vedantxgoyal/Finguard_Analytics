-- =============================================================================
-- Queries to generate findings_report.md content. Run via:
--   psql $env:DATABASE_URL_DIRECT -f findings_queries.sql
-- =============================================================================

-- 1. Overall sample composition (confirms the sampling methodology numbers)
SELECT
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE is_fraud) AS fraud_rows,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_fraud) / COUNT(*), 4) AS sample_fraud_rate_pct
FROM fact_transactions;

-- 2. Fraud rate by transaction type (confirms fraud is concentrated in
--    TRANSFER/CASH_OUT only, per PaySim's known construction)
SELECT
    tt.type_name,
    COUNT(*) AS total_transactions,
    COUNT(*) FILTER (WHERE f.is_fraud) AS fraud_transactions,
    ROUND(100.0 * COUNT(*) FILTER (WHERE f.is_fraud) / COUNT(*), 4) AS fraud_rate_pct,
    SUM(f.amount) FILTER (WHERE f.is_fraud) AS total_fraud_amount
FROM fact_transactions f
JOIN dim_transaction_type tt ON f.transaction_type_key = tt.transaction_type_key
GROUP BY tt.type_name
ORDER BY fraud_rate_pct DESC;

-- 3. Full segment breakdown (type x amount bucket) - the core finding
SELECT * FROM mv_segment_fraud_rates ORDER BY fraud_rate_pct DESC;

-- 4. Detection rule performance: PaySim's isFlaggedFraud vs actual isFraud
SELECT
    SUM(flagged_fraud_count) AS total_flagged,
    SUM(false_positive_flags) AS false_positives,
    SUM(missed_fraud_count) AS missed_fraud,
    (SELECT COUNT(*) FROM fact_transactions WHERE is_fraud) AS total_actual_fraud,
    ROUND(
        100.0 * (SUM(missed_fraud_count))::numeric /
        GREATEST((SELECT COUNT(*) FROM fact_transactions WHERE is_fraud), 1),
        2
    ) AS pct_fraud_missed_by_rule
FROM mv_hourly_fraud_summary;

-- 5. Top 10 highest-velocity fraud accounts (mule-account-style signal)
SELECT
    account_id, account_type, total_transactions, fraud_transactions,
    distinct_counterparties, total_amount_sent, active_step_span,
    avg_transactions_per_step
FROM mv_account_velocity
WHERE fraud_transactions > 0
ORDER BY fraud_transactions DESC, total_amount_sent DESC
LIMIT 10;

-- 6. Time concentration: are fraud transactions clustered in specific steps?
SELECT step, total_transactions, fraud_transactions, fraud_rate_pct
FROM mv_hourly_fraud_summary
ORDER BY fraud_rate_pct DESC
LIMIT 10;

-- 7. Database footprint (for the architecture/constraints section)
SELECT pg_size_pretty(pg_database_size(current_database())) AS total_db_size;
SELECT
    relname AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;