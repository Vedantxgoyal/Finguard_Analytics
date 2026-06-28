"""
routers/overview.py — Dashboard landing page: aggregate KPIs and the
hourly fraud time-series chart.

Backed entirely by mv_hourly_fraud_summary (db/materialized_views.sql).
Neither endpoint here ever queries fact_transactions directly — that's
the whole point of this project's architecture (see project README /
materialized_views.sql header comment).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas import HourlyFraudSummary, OverviewKPIs

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("/kpis", response_model=OverviewKPIs)
async def get_overview_kpis(db: AsyncSession = Depends(get_db)) -> OverviewKPIs:
    """
    Top-line KPI strip for the dashboard landing page: totals and overall
    fraud rate across the entire loaded dataset (all steps).

    Computed via a single SQL aggregate query over mv_hourly_fraud_summary
    (743 rows) rather than fetched-then-summed in Python — both faster and
    avoids re-deriving Decimal-safe summation logic in the application
    layer when Postgres already does this correctly and efficiently.
    """
    query = text(
        """
        SELECT
            SUM(total_transactions)            AS total_transactions,
            SUM(fraud_transactions)             AS total_fraud_transactions,
            ROUND(
                100.0 * SUM(fraud_transactions) / GREATEST(SUM(total_transactions), 1),
                4
            )                                    AS overall_fraud_rate_pct,
            SUM(total_amount)                   AS total_amount,
            COALESCE(SUM(fraud_amount), 0)       AS total_fraud_amount,
            SUM(flagged_fraud_count)             AS total_flagged_fraud_count,
            SUM(false_positive_flags)            AS total_false_positive_flags,
            SUM(missed_fraud_count)              AS total_missed_fraud_count,
            MIN(step)                            AS step_range_min,
            MAX(step)                            AS step_range_max
        FROM mv_hourly_fraud_summary
        """
    )
    result = await db.execute(query)
    row = result.one()
    return OverviewKPIs.model_validate(row)


@router.get("/timeseries", response_model=list[HourlyFraudSummary])
async def get_hourly_timeseries(
    step_min: int | None = Query(
        None, ge=1, description="Inclusive lower bound on step. Omit for no lower bound."
    ),
    step_max: int | None = Query(
        None, ge=1, description="Inclusive upper bound on step. Omit for no upper bound."
    ),
    db: AsyncSession = Depends(get_db),
) -> list[HourlyFraudSummary]:
    """
    Hourly (per-step) transaction/fraud time series, optionally windowed
    by step_min/step_max for the frontend's zoom/pan interaction. With no
    bounds given, returns all 743 rows — small enough that pagination
    would be premature complexity for this endpoint.

    step_min/step_max are bound as SQL parameters (never string-
    interpolated), satisfying the project's parameterized-queries-only
    requirement and incidentally making this endpoint immune to SQL
    injection via these inputs regardless.
    """
    conditions = []
    params: dict[str, int] = {}

    if step_min is not None:
        conditions.append("step >= :step_min")
        params["step_min"] = step_min
    if step_max is not None:
        conditions.append("step <= :step_max")
        params["step_max"] = step_max

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    query = text(
        f"""
        SELECT
            step, total_transactions, fraud_transactions, fraud_rate_pct,
            total_amount, fraud_amount, avg_amount, flagged_fraud_count,
            false_positive_flags, missed_fraud_count
        FROM mv_hourly_fraud_summary
        {where_clause}
        ORDER BY step
        """
    )
    result = await db.execute(query, params)
    rows = result.all()
    return [HourlyFraudSummary.model_validate(row) for row in rows]