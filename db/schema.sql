-- =============================================================================
-- FinGuard Analytics — Star Schema for PaySim Transaction Data
-- =============================================================================
-- Source: PaySim synthetic mobile-money dataset (6,362,620 rows)
-- https://www.kaggle.com/datasets/ealaxi/paysim1
--
-- DESIGN DECISIONS (see chat log for full rationale — summarized here so the
-- schema is self-documenting for anyone reviewing the repo cold):
--
-- 1. No dim_time table. `step` (1-743, hourly) is kept as a degenerate
--    dimension directly on fact_transactions. PaySim's `step` does not map to
--    real calendar dates — there are no calendar attributes (day-of-week,
--    month, holiday flags) to justify a separate dimension. A join table for
--    743 distinct integers would add join cost with zero analytical payoff.
--    This deviates from textbook star-schema dogma deliberately.
--
-- 2. Unified dim_account (not dim_customer / dim_merchant split). nameOrig
--    and nameDest share one ID space (C-prefix = customer, M-prefix =
--    merchant) and the same account can appear as origin in one row and
--    destination in another. Splitting into two tables would force
--    prefix-based CASE logic on every join. One table, one `account_type`
--    column, two FKs from the fact table (orig_account_id, dest_account_id).
--
-- 3. dim_transaction_type is normalized (5 rows) purely for dimensional-
--    modeling signal — at this cardinality it has no performance benefit
--    over a VARCHAR/CHECK-constrained column on the fact table directly.
--    Kept normalized because this project is explicitly scoped for
--    Analytics Engineer signal, not because it's required.
--
-- 4. All monetary columns are NUMERIC(18,2), never FLOAT/DOUBLE. Financial
--    amounts use exact decimal arithmetic — this is non-negotiable for any
--    project framed around fraud/financial analytics. PaySim amounts are
--    well within NUMERIC(18,2) range (max ~92M in source data).
--
-- 5. isFraud / isFlaggedFraud are BOOLEAN, cast from source 0/1 during load.
--    Same storage cost as SMALLINT in Postgres, cleaner semantics downstream
--    (FastAPI/Pydantic, SQL predicates read as `WHERE is_fraud` not
--    `WHERE is_fraud = 1`).
--
-- 6. Surrogate keys (BIGSERIAL) on all dimension tables, never natural keys
--    (e.g. raw account_id string) as PKs — insulates the schema from source
--    data quirks and keeps FK columns in fact_transactions as BIGINT
--    (8 bytes) rather than VARCHAR comparisons on every join.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------
-- pg_stat_statements is enabled for query performance diagnostics, which is
-- a real production concern on a free-tier instance with limited resources
-- and directly supports the "why is this slow" interview talking point.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- -----------------------------------------------------------------------------
-- Dimension: dim_account
-- -----------------------------------------------------------------------------
-- One row per distinct account ID seen in either nameOrig or nameDest across
-- the source data. account_type is derived from the PaySim ID prefix:
--   'C...' -> CUSTOMER
--   'M...' -> MERCHANT
-- This derivation happens in load_data.py at ingest time, not in SQL,
-- because it's a one-time deterministic transform applied to ~6.36M rows of
-- raw input — doing it in Python during the batched load avoids a separate
-- UPDATE pass over the full fact table after the fact.
CREATE TABLE dim_account (
    account_key      BIGSERIAL PRIMARY KEY,
    account_id       VARCHAR(20)  NOT NULL,
    account_type     VARCHAR(10)  NOT NULL,

    CONSTRAINT uq_dim_account_account_id UNIQUE (account_id),
    CONSTRAINT chk_dim_account_type CHECK (account_type IN ('CUSTOMER', 'MERCHANT'))
);

COMMENT ON TABLE dim_account IS
    'Unified dimension for all account IDs (customer and merchant) seen as '
    'either transaction origin or destination. account_type derived from '
    'PaySim ID prefix (C=customer, M=merchant) during load.';

-- -----------------------------------------------------------------------------
-- Dimension: dim_transaction_type
-- -----------------------------------------------------------------------------
-- Static lookup, 5 known PaySim transaction types. Seeded explicitly below
-- rather than discovered from source data, since the value set is fixed and
-- documented by the dataset itself — no reason to infer it at load time.
CREATE TABLE dim_transaction_type (
    transaction_type_key   SMALLSERIAL PRIMARY KEY,
    type_name               VARCHAR(20) NOT NULL,

    CONSTRAINT uq_dim_transaction_type_name UNIQUE (type_name)
);

COMMENT ON TABLE dim_transaction_type IS
    'Lookup of the 5 PaySim transaction types: PAYMENT, TRANSFER, CASH_OUT, '
    'CASH_IN, DEBIT. Normalized for dimensional-modeling signal; not '
    'required for performance at this cardinality.';

INSERT INTO dim_transaction_type (type_name) VALUES
    ('PAYMENT'),
    ('TRANSFER'),
    ('CASH_OUT'),
    ('CASH_IN'),
    ('DEBIT')
ON CONFLICT (type_name) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Fact: fact_transactions
-- -----------------------------------------------------------------------------
-- Grain: one row per transaction. 6,362,620 rows expected after load.
--
-- step is kept as a plain INTEGER (degenerate dimension) — see header note.
-- orig_account_id / dest_account_id are FKs into dim_account.
-- transaction_type_key is FK into dim_transaction_type.
CREATE TABLE fact_transactions (
    transaction_key       BIGSERIAL PRIMARY KEY,

    step                   INTEGER        NOT NULL,
    transaction_type_key   SMALLINT       NOT NULL REFERENCES dim_transaction_type (transaction_type_key),

    amount                 NUMERIC(18,2)  NOT NULL,

    orig_account_key       BIGINT         NOT NULL REFERENCES dim_account (account_key),
    old_balance_orig       NUMERIC(18,2)  NOT NULL,
    new_balance_orig       NUMERIC(18,2)  NOT NULL,

    dest_account_key       BIGINT         NOT NULL REFERENCES dim_account (account_key),
    old_balance_dest       NUMERIC(18,2)  NOT NULL,
    new_balance_dest       NUMERIC(18,2)  NOT NULL,

    is_fraud               BOOLEAN        NOT NULL DEFAULT FALSE,
    is_flagged_fraud       BOOLEAN        NOT NULL DEFAULT FALSE,

    CONSTRAINT chk_fact_transactions_step_positive CHECK (step > 0),
    CONSTRAINT chk_fact_transactions_amount_nonneg CHECK (amount >= 0)
);

COMMENT ON TABLE fact_transactions IS
    'Transaction fact table, one row per PaySim transaction. '
    'Expected row count: 6,362,620.';

COMMENT ON COLUMN fact_transactions.step IS
    'Hourly time step from source data (1-743). Degenerate dimension — no '
    'separate dim_time table; see schema header for rationale.';

-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------
-- Rationale stated per index — every index here is justified by a known
-- query pattern from the locked architecture (materialized view refresh,
-- velocity drill-down endpoint, or fraud-rate filtering), not added
-- speculatively. Unjustified indexes on a 6.36M-row table cost write
-- throughput on load and disk space on a free-tier instance for no benefit.

-- Fraud is ~0.13% of rows in PaySim — highly selective predicate, used by
-- every fraud-pattern query and the materialized views that aggregate fraud
-- rates by segment/type/time.
CREATE INDEX idx_fact_transactions_is_fraud
    ON fact_transactions (is_fraud)
    WHERE is_fraud = TRUE;
-- Partial index: only fraud rows are indexed. At 0.13% selectivity this
-- index is tiny (~8k rows) relative to a full 6.36M-row index, and every
-- real query against this column filters FOR fraud, never for non-fraud
-- specifically — so a partial index is strictly better than a full one here.

-- Every materialized view buckets by step (hourly fraud summary, velocity
-- windows). A plain b-tree supports both equality and range scans.
CREATE INDEX idx_fact_transactions_step
    ON fact_transactions (step);

-- Velocity drill-down endpoint (backend/routers/velocity.py) needs: "all
-- transactions for account X, ordered by time". Composite index supports
-- both the equality filter on the account and the range/order on step
-- without a separate sort step.
CREATE INDEX idx_fact_transactions_orig_account_step
    ON fact_transactions (orig_account_key, step);

-- Same justification as above, for the destination side — velocity/fraud
-- patterns frequently care about money flowing INTO an account too
-- (e.g. mule-account detection patterns: many small inbound transfers
-- followed by a CASH_OUT).
CREATE INDEX idx_fact_transactions_dest_account_step
    ON fact_transactions (dest_account_key, step);

-- Supports filtering/joining by transaction type, used by fraud_patterns.py
-- router and the segment-fraud-rate materialized view.
CREATE INDEX idx_fact_transactions_type
    ON fact_transactions (transaction_type_key);

-- No index on amount alone (see header note): range scans on amount go
-- through the fraud-pattern materialized view, which precomputes
-- amount-bucket aggregates, not a raw scan of the fact table.

CREATE INDEX idx_dim_account_account_type
    ON dim_account (account_type);
-- Supports "fraud rate by account type" style queries without a full scan
-- of dim_account (small table, but this index also lets the planner avoid
-- touching dim_account at all for some queries via index-only scans).

-- -----------------------------------------------------------------------------
-- Notes for load_data.py
-- -----------------------------------------------------------------------------
-- Load order matters due to FK constraints:
--   1. dim_transaction_type — already seeded above, no action needed.
--   2. dim_account — must be fully populated (all distinct nameOrig +
--      nameDest values) BEFORE any fact_transactions insert, since every
--      fact row FKs into it twice.
--   3. fact_transactions — bulk load via COPY or batched INSERT, resolving
--      account_id -> account_key and type_name -> transaction_type_key via
--      a lookup dict built from steps 1-2 (do NOT do this via per-row JOIN
--      at insert time — build the dict once in memory, it's small: dim_account
--      has at most ~9M distinct accounts in the worst case, dim_transaction_type
--      has 5 rows).