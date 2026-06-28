#!/usr/bin/env python3
"""
refresh_views.py — Refresh all materialized views after fact_transactions
or dim_account data changes.

Runs:
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_hourly_fraud_summary;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_segment_fraud_rates;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_account_velocity;

WHY CONCURRENTLY
-----------------
A plain REFRESH MATERIALIZED VIEW takes an ACCESS EXCLUSIVE lock on the
view for the duration of the refresh — any concurrent SELECT against it
blocks until the refresh finishes. CONCURRENTLY avoids this (readers keep
seeing the old data until the new data is ready, then it swaps atomically)
at the cost of requiring a unique index on the view (already created in
materialized_views.sql) and being somewhat slower. This is the correct
trade-off for a view that a live FastAPI app may be querying at any time —
the views in this project are explicitly designed to be the thing the
frontend hits on every filter interaction, so blocking reads during a
refresh would mean visible downtime on the dashboard.

REFRESH ORDER
-------------
mv_account_velocity is refreshed last because it's the most expensive
(joins the full fact table against dim_account) — a failure there
shouldn't prevent the two cheaper views from being up to date. Each
view's refresh is independent of the others (none depend on each other,
only on the base tables), so there's no correctness reason to enforce a
specific order beyond this cost-based one.

EACH-VIEW INDEPENDENCE / PARTIAL FAILURE
------------------------------------------
If one view's refresh fails, this script logs the failure, continues to
attempt the remaining views, and exits non-zero overall — a stale
mv_segment_fraud_rates shouldn't block mv_account_velocity from getting
fresh data, since they serve different endpoints/routers.

USAGE
-----
    python db/refresh_views.py
    python db/refresh_views.py --view mv_hourly_fraud_summary   # refresh just one
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import psycopg2
from dotenv import load_dotenv

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
log = logging.getLogger("refresh_views")


class RefreshError(RuntimeError):
    """Raised when a view refresh fails in a way that should be reported clearly."""


# Order matters for cost reasons (see module docstring), not correctness —
# each view's refresh is independent of the others.
VIEWS_IN_REFRESH_ORDER = [
    "mv_hourly_fraud_summary",
    "mv_segment_fraud_rates",
    "mv_account_velocity",
]

# REFRESH MATERIALIZED VIEW CONCURRENTLY temporarily needs disk space for
# BOTH the old and new view contents simultaneously (it builds the new
# version alongside the old one, then swaps). On a free-tier instance
# already near its storage cap, this can fail with DiskFull even though a
# plain (non-concurrent) refresh of the same view succeeds, since plain
# REFRESH rebuilds in place without that temporary doubling.
#
# mv_account_velocity is the largest/most expensive view (858K+ rows,
# joins the full fact table) and is the one observed to hit this in
# practice on a ~362MB/500MB-cap database — confirmed via DiskFull during
# testing, not a hypothetical concern. It defaults to non-concurrent
# refresh as a result. The trade-off: a brief ACCESS EXCLUSIVE lock during
# its refresh (readers block until it completes) instead of no blocking.
# Given this view changes only when fact_transactions/dim_account changes
# (i.e. after a data reload, not on a tight schedule), a short blocking
# window here is an acceptable, deliberate trade against running out of
# disk on a constrained free-tier instance.
VIEWS_REQUIRING_NON_CONCURRENT_REFRESH = {"mv_account_velocity"}


def get_connection():
    """
    Open a psycopg2 connection using DATABASE_URL_DIRECT.

    REFRESH MATERIALIZED VIEW CONCURRENTLY runs its own internal
    transaction and cannot run inside an explicit transaction block
    alongside other statements — using the same session-mode connection
    as schema.sql/load_data.py (not the transaction-mode pooler) avoids
    any pooler-level transaction wrapping that could interfere with this.
    """
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL_DIRECT")
    if not dsn:
        raise RefreshError(
            "DATABASE_URL_DIRECT is not set. Check that .env exists and "
            "has been populated (see .env.example for the expected format)."
        )
    try:
        conn = psycopg2.connect(dsn)
    except psycopg2.OperationalError as exc:
        raise RefreshError(f"Could not connect to database: {exc}") from exc
    # CONCURRENTLY refresh cannot run inside a multi-statement transaction
    # block; autocommit ensures each REFRESH statement is its own
    # transaction, which is what CONCURRENTLY requires.
    conn.autocommit = True
    return conn


def refresh_one_view(conn, view_name: str) -> float:
    """
    Refresh a single materialized view. Uses CONCURRENTLY by default
    (non-blocking for readers), except for views listed in
    VIEWS_REQUIRING_NON_CONCURRENT_REFRESH, which use a plain REFRESH to
    avoid CONCURRENTLY's temporary double-storage requirement on a
    storage-constrained instance (see module-level comment).

    Returns elapsed seconds on success; raises RefreshError with a clear
    message on failure (including the common "no unique index" and
    "disk full" cases, which otherwise produce cryptic Postgres errors).
    """
    use_concurrent = view_name not in VIEWS_REQUIRING_NON_CONCURRENT_REFRESH
    refresh_sql = (
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name};"
        if use_concurrent
        else f"REFRESH MATERIALIZED VIEW {view_name};"
    )

    log.info(
        "Refreshing %s (%s) ...",
        view_name, "concurrent" if use_concurrent else "non-concurrent, blocking",
    )
    started = time.monotonic()
    try:
        with conn.cursor() as cur:
            cur.execute(refresh_sql)
    except psycopg2.errors.ObjectNotInPrerequisiteState as exc:
        raise RefreshError(
            f"{view_name} cannot be refreshed CONCURRENTLY — it has no "
            "unique index. This should not happen if materialized_views.sql "
            "was applied as-is; check that the corresponding "
            "CREATE UNIQUE INDEX statement ran successfully."
        ) from exc
    except psycopg2.errors.UndefinedTable as exc:
        raise RefreshError(
            f"{view_name} does not exist. Has materialized_views.sql been "
            "applied to this database yet?"
        ) from exc
    except psycopg2.errors.DiskFull as exc:
        hint = (
            " Consider adding this view to VIEWS_REQUIRING_NON_CONCURRENT_REFRESH "
            "to avoid CONCURRENTLY's temporary double-storage requirement."
            if use_concurrent else
            " Even a non-concurrent refresh ran out of disk — the database "
            "itself is at or near its storage cap; free up space (see "
            "load_data.py's sampling configuration) before retrying."
        )
        raise RefreshError(f"Out of disk space while refreshing {view_name}.{hint}") from exc
    elapsed = time.monotonic() - started
    log.info("Refreshed %s in %.2fs", view_name, elapsed)
    return elapsed


def refresh_all(conn, views: list[str]) -> dict[str, float | None]:
    """
    Refresh each view in turn. Continues past individual failures so one
    bad view doesn't prevent the others from being refreshed (see module
    docstring). Returns a dict of view_name -> elapsed_seconds, with None
    for any view that failed.
    """
    results: dict[str, float | None] = {}
    for view_name in views:
        try:
            results[view_name] = refresh_one_view(conn, view_name)
        except RefreshError as exc:
            log.error("Failed to refresh %s: %s", view_name, exc)
            results[view_name] = None
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--view",
        choices=VIEWS_IN_REFRESH_ORDER,
        default=None,
        help="Refresh only this specific view, instead of all three.",
    )
    args = parser.parse_args()

    views_to_refresh = [args.view] if args.view else VIEWS_IN_REFRESH_ORDER

    conn = None
    try:
        conn = get_connection()
    except RefreshError as exc:
        log.error("Cannot proceed: %s", exc)
        return 1

    try:
        started = time.monotonic()
        results = refresh_all(conn, views_to_refresh)
        total_elapsed = time.monotonic() - started

        failed = [name for name, elapsed in results.items() if elapsed is None]
        succeeded = [name for name, elapsed in results.items() if elapsed is not None]

        log.info(
            "Refresh run complete in %.2fs: %d succeeded, %d failed.",
            total_elapsed, len(succeeded), len(failed),
        )

        if failed:
            log.error("Failed views: %s", ", ".join(failed))
            return 1

        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())