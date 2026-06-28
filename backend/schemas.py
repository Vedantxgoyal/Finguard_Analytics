"""
schemas.py — Pydantic response and query-parameter models for the FinGuard
Analytics API.

Each response schema below maps 1:1 to the columns of one materialized
view (see db/materialized_views.sql) and is consumed by exactly one
router:

    HourlyFraudSummary  <- mv_hourly_fraud_summary  <- routers/overview.py
    SegmentFraudRate    <- mv_segment_fraud_rates   <- routers/fraud_patterns.py
    AccountVelocity     <- mv_account_velocity      <- routers/velocity.py

All response models set `model_config = ConfigDict(from_attributes=True)`
(Pydantic v2's replacement for v1's `orm_mode`), which lets a router do:

    result = await db.execute(select(...))
    rows = result.all()
    return [HourlyFraudSummary.model_validate(row) for row in rows]

without manually unpacking each SQLAlchemy Row into keyword arguments.

NUMERIC TYPES: Postgres NUMERIC columns arrive via asyncpg as Python
Decimal, not float. Schemas below type these fields as Decimal (not
float) to avoid silent precision loss — this matters for monetary fields
specifically, consistent with schema.sql's choice of NUMERIC(18,2) over
FLOAT for the same reason. FastAPI/Pydantic serializes Decimal to JSON as
a string by default in strict mode, or a number depending on response
model config; routers should be explicit about which behavior they want
rather than relying on the default if this matters to the frontend.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# -----------------------------------------------------------------------------
# Shared enums
# -----------------------------------------------------------------------------

class TransactionType(str, Enum):
    """
    Mirrors db/schema.sql's dim_transaction_type seed values exactly.
    Defined as an enum (not a bare str) so FastAPI auto-generates a
    dropdown/restricted-value field in the OpenAPI docs for any query
    parameter typed with this, and so an invalid type value is rejected
    by FastAPI's request validation before it ever reaches a query.
    """
    PAYMENT = "PAYMENT"
    TRANSFER = "TRANSFER"
    CASH_OUT = "CASH_OUT"
    CASH_IN = "CASH_IN"
    DEBIT = "DEBIT"


class AmountBucket(str, Enum):
    """Mirrors the CASE expression bucket labels in mv_segment_fraud_rates."""
    BUCKET_0_1K = "0-1K"
    BUCKET_1K_10K = "1K-10K"
    BUCKET_10K_100K = "10K-100K"
    BUCKET_100K_1M = "100K-1M"
    BUCKET_1M_PLUS = "1M+"


class AccountType(str, Enum):
    """Mirrors dim_account.account_type's CHECK constraint values."""
    CUSTOMER = "CUSTOMER"
    MERCHANT = "MERCHANT"


# -----------------------------------------------------------------------------
# overview.py — backed by mv_hourly_fraud_summary
# -----------------------------------------------------------------------------

class HourlyFraudSummary(BaseModel):
    """One row of mv_hourly_fraud_summary — one PaySim time step (hour)."""

    model_config = ConfigDict(from_attributes=True)

    step: int = Field(..., description="PaySim hourly time step (1-743).")
    total_transactions: int
    fraud_transactions: int
    fraud_rate_pct: Decimal = Field(..., description="Fraud rate as a percentage, e.g. 0.9570 means 0.957%.")
    total_amount: Decimal
    fraud_amount: Decimal | None = Field(
        None, description="Null when fraud_transactions is 0 for this step."
    )
    avg_amount: Decimal
    flagged_fraud_count: int
    false_positive_flags: int = Field(
        ..., description="Flagged as fraud by PaySim's rule but not actually fraud."
    )
    missed_fraud_count: int = Field(
        ..., description="Actually fraud but not flagged by PaySim's rule."
    )


class OverviewKPIs(BaseModel):
    """
    Aggregate KPI strip for the dashboard landing page — summed/derived
    across ALL steps, not per-step. Computed by overview.py from
    HourlyFraudSummary rows rather than backed by its own materialized
    view, since it's a simple aggregate of an already-small (743-row)
    result set — a separate view for this would be redundant.
    """

    model_config = ConfigDict(from_attributes=True)

    total_transactions: int
    total_fraud_transactions: int
    overall_fraud_rate_pct: Decimal
    total_amount: Decimal
    total_fraud_amount: Decimal
    total_flagged_fraud_count: int
    total_false_positive_flags: int
    total_missed_fraud_count: int
    step_range_min: int
    step_range_max: int


# -----------------------------------------------------------------------------
# fraud_patterns.py — backed by mv_segment_fraud_rates
# -----------------------------------------------------------------------------

class SegmentFraudRate(BaseModel):
    """One row of mv_segment_fraud_rates — one (type, amount_bucket) segment."""

    model_config = ConfigDict(from_attributes=True)

    type_name: TransactionType
    amount_bucket: AmountBucket
    amount_bucket_sort_order: int = Field(
        ..., description="Use to order buckets numerically; do not sort amount_bucket as a string."
    )
    total_transactions: int
    fraud_transactions: int
    fraud_rate_pct: Decimal
    total_amount: Decimal
    fraud_amount: Decimal | None = None
    avg_amount: Decimal


class FraudPatternFilter(BaseModel):
    """
    Query-parameter schema for GET /fraud-patterns/. All fields optional —
    an unfiltered request returns all 23 (or fewer, if data changes)
    populated segments. FastAPI binds these automatically when used as
    query-param dependencies (Depends(FraudPatternFilter) or individual
    Query(...) params in the router — router implementation decides which
    style fits FastAPI's docs generation best).
    """

    type_name: TransactionType | None = Field(
        None, description="Filter to a single transaction type. Omit for all types."
    )
    amount_bucket: AmountBucket | None = Field(
        None, description="Filter to a single amount bucket. Omit for all buckets."
    )
    min_fraud_rate_pct: Decimal | None = Field(
        None, ge=0, le=100,
        description="Only return segments with fraud_rate_pct >= this value.",
    )


# -----------------------------------------------------------------------------
# velocity.py — backed by mv_account_velocity
# -----------------------------------------------------------------------------

class AccountVelocity(BaseModel):
    """One row of mv_account_velocity — one account's outbound activity profile."""

    model_config = ConfigDict(from_attributes=True)

    account_key: int = Field(..., description="Internal surrogate key, not the PaySim account_id string.")
    account_id: str = Field(..., description="PaySim account ID, e.g. 'C1231006815'.")
    account_type: AccountType

    total_transactions: int
    fraud_transactions: int
    distinct_counterparties: int

    total_amount_sent: Decimal
    avg_amount_sent: Decimal
    max_amount_sent: Decimal

    first_step: int
    last_step: int
    active_step_span: int = Field(
        ..., description="last_step - first_step. 0 for an account with exactly one transaction."
    )
    avg_transactions_per_step: Decimal


class AccountNotFoundError(BaseModel):
    """
    Standard 404 response body for GET /velocity/{account_id} when the
    account_id either doesn't exist in dim_account at all, or exists but
    was never an ORIGIN of any transaction (and therefore has no row in
    mv_account_velocity by that view's design — see materialized_views.sql).
    """

    detail: str
    account_id: str