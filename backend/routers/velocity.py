"""
routers/velocity.py — Per-account velocity drill-down.

Backed by mv_account_velocity (db/materialized_views.sql), which is
ORIGIN-only by design (see that file's header comment): an account only
has a row here if it appears as orig_account_key in at least one
fact_transactions row. This router distinguishes two different "not
found" situations rather than collapsing them into one generic 404 —
see get_account_velocity() docstring for why that distinction matters.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.schemas import AccountVelocity

router = APIRouter(prefix="/velocity", tags=["velocity"])


@router.get("/{account_id}", response_model=AccountVelocity)
async def get_account_velocity(
    account_id: str,
    db: AsyncSession = Depends(get_db),
) -> AccountVelocity:
    """
    Outbound transaction velocity profile for a single account.

    account_id is the PaySim-format string (e.g. "C1231006815" or
    "M1979787155"), not the internal account_key surrogate — this is
    what the frontend has on hand (it's the ID visible in any
    fraud-pattern drill-down), so the API accepts it directly rather
    than requiring a separate lookup step first.

    TWO DISTINCT 404 CASES, not collapsed into one:
      1. account_id does not exist in dim_account at all (typo, or an ID
         that was sampled out of dim_account during the load — see
         db/load_data.py's stratified sampling, which only retains
         accounts that appear in a SAMPLED-IN row).
      2. account_id exists in dim_account but has zero rows as an
         ORIGIN in fact_transactions — e.g. an account that only ever
         received money (common for merchants). mv_account_velocity
         has no row for it by design, but the account itself is real.

    These get different error messages because they mean different
    things to a frontend/analyst: case 1 is "this ID may be wrong or
    wasn't sampled into this dataset"; case 2 is "this ID is real but
    has no outbound activity to show". Collapsing them into a generic
    "not found" would be a worse error message for both cases.
    """
    velocity_query = text(
        """
        SELECT
            account_key, account_id, account_type, total_transactions,
            fraud_transactions, distinct_counterparties, total_amount_sent,
            avg_amount_sent, max_amount_sent, first_step, last_step,
            active_step_span, avg_transactions_per_step
        FROM mv_account_velocity
        WHERE account_id = :account_id
        """
    )
    result = await db.execute(velocity_query, {"account_id": account_id})
    row = result.one_or_none()

    if row is not None:
        return AccountVelocity.model_validate(row)

    # No velocity row — figure out which of the two 404 cases this is
    # before responding, so the error message is actually useful.
    exists_query = text("SELECT 1 FROM dim_account WHERE account_id = :account_id")
    exists_result = await db.execute(exists_query, {"account_id": account_id})
    account_exists = exists_result.one_or_none() is not None

    if account_exists:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Account '{account_id}' exists but has no outbound "
                "transactions (it was never an origin of any sampled "
                "transaction, only possibly a destination)."
            ),
        )

    raise HTTPException(
        status_code=404,
        detail=(
            f"Account '{account_id}' was not found. It may not exist in "
            "the source dataset, or it may have been excluded by this "
            "project's stratified non-fraud sampling (see db/load_data.py)."
        ),
    )