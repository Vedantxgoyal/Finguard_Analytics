"""
routers/fraud_patterns.py — Fraud-pattern explorer: filterable fraud rate
and volume by transaction type and amount bucket.

Backed entirely by mv_segment_fraud_rates (db/materialized_views.sql).
This is the endpoint behind the frontend's segment filter/heatmap UI —
the user picks a type and/or amount bucket and/or a minimum fraud-rate
threshold, and gets back the matching pre-aggregated segments instantly,
with no scan of the raw 858K-row fact table at request time.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas import AmountBucket, SegmentFraudRate, TransactionType

router = APIRouter(prefix="/fraud-patterns", tags=["fraud-patterns"])


@router.get("/", response_model=list[SegmentFraudRate])
async def get_fraud_patterns(
    type_name: TransactionType | None = Query(
        None, description="Filter to a single transaction type. Omit for all types."
    ),
    amount_bucket: AmountBucket | None = Query(
        None, description="Filter to a single amount bucket. Omit for all buckets."
    ),
    min_fraud_rate_pct: Decimal | None = Query(
        None, ge=0, le=100,
        description="Only return segments with fraud_rate_pct >= this value.",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[SegmentFraudRate]:
    """
    Filtered fraud-rate segments. All filters are optional and combine
    with AND. With no filters, returns all populated (type, amount_bucket)
    segments (23 as of the current load — see db verification output),
    ordered by type then amount bucket's numeric sort order (never by
    amount_bucket's string value, which would sort "100K-1M" before
    "1K-10K" lexicographically).

    type_name and amount_bucket are validated against the TransactionType
    / AmountBucket enums by FastAPI before this function body even runs —
    an invalid value is rejected with a 422 automatically, never reaches
    the SQL layer.
    """
    conditions = []
    params: dict[str, object] = {}

    if type_name is not None:
        conditions.append("type_name = :type_name")
        params["type_name"] = type_name.value
    if amount_bucket is not None:
        conditions.append("amount_bucket = :amount_bucket")
        params["amount_bucket"] = amount_bucket.value
    if min_fraud_rate_pct is not None:
        conditions.append("fraud_rate_pct >= :min_fraud_rate_pct")
        params["min_fraud_rate_pct"] = min_fraud_rate_pct

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    query = text(
        f"""
        SELECT
            type_name, amount_bucket, amount_bucket_sort_order,
            total_transactions, fraud_transactions, fraud_rate_pct,
            total_amount, fraud_amount, avg_amount
        FROM mv_segment_fraud_rates
        {where_clause}
        ORDER BY type_name, amount_bucket_sort_order
        """
    )
    result = await db.execute(query, params)
    rows = result.all()
    return [SegmentFraudRate.model_validate(row) for row in rows]


@router.get("/top-risk", response_model=list[SegmentFraudRate])
async def get_top_risk_segments(
    limit: int = Query(5, ge=1, le=23, description="Number of highest-fraud-rate segments to return."),
    min_transactions: int = Query(
        50, ge=1,
        description=(
            "Exclude segments with fewer than this many total transactions. "
            "Prevents a segment with e.g. 2 transactions and 1 fraud case "
            "(50% fraud rate) from outranking a segment with thousands of "
            "transactions and a genuinely high but lower rate — a small-n "
            "statistical-significance guard, not an arbitrary filter."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> list[SegmentFraudRate]:
    """
    The highest fraud-rate segments, for a dashboard "where's the risk
    concentrated" callout. Distinct from GET / because this endpoint
    applies a minimum-volume floor (min_transactions) before ranking by
    rate — without that floor, tiny-sample segments with artificially
    high rates would dominate the top of the list.
    """
    query = text(
        """
        SELECT
            type_name, amount_bucket, amount_bucket_sort_order,
            total_transactions, fraud_transactions, fraud_rate_pct,
            total_amount, fraud_amount, avg_amount
        FROM mv_segment_fraud_rates
        WHERE total_transactions >= :min_transactions
        ORDER BY fraud_rate_pct DESC
        LIMIT :limit
        """
    )
    result = await db.execute(query, {"min_transactions": min_transactions, "limit": limit})
    rows = result.all()
    return [SegmentFraudRate.model_validate(row) for row in rows]