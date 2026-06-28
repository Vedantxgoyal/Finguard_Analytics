#!/usr/bin/env python3
"""
load_data.py — Bulk-load the PaySim CSV into the FinGuard Analytics star schema.

STRATIFIED SAMPLING (default behavior)
----------------------------------------
Free-tier managed Postgres (Supabase/Neon) enforces a ~500MB database-size
quota that flips the database to read-only once exceeded — confirmed
empirically against this exact dataset/schema. Loading 100% of PaySim's
6,362,620 rows plus full indexing does not fit.

This script therefore loads a STRATIFIED SAMPLE by default:
  - 100% of fraud rows (isFraud = true) — ~8,213 rows, the entire
    analytical point of a fraud project, so these are never dropped.
  - A deterministic ~13.4% sample of non-fraud rows, targeting ~850,000
    rows, sized from an empirical measurement (100,000 fact rows + their
    proportional dim_account/index overhead measured at 48.4MB on this
    schema -> ~484 bytes/row all-in -> 400MB budget / 484 bytes ≈ 858,000
    total rows, leaving ~100MB headroom under the 500MB cap for
    materialized views built later in this project).

Sampling is deterministic (seeded MD5 hash of stable row-identifying
fields — see should_include_row()), not a per-row RNG draw, so the exact
same rows are selected on every run, and pass 1 / pass 2 independently
agree on which rows survive without needing to share state between them.

Both TARGET_NON_FRAUD_ROWS and the resulting NON_FRAUD_SAMPLE_RATE are
documented in findings/findings_report.md as the project's sampling
methodology — this is real production trade-off reasoning under an
infrastructure constraint, not a shortcut.

ARCHITECTURE
------------
Two-pass load over the source CSV:

  Pass 1 (dimension build):
      Stream the CSV once, apply the same sampling filter used in pass 2,
      collect every distinct account ID seen in either `nameOrig` or
      `nameDest` AMONG SAMPLED-IN ROWS ONLY, derive `account_type` from
      the ID prefix (C -> CUSTOMER, M -> MERCHANT), and bulk-insert the
      result into `dim_account` via COPY. The set of distinct accounts
      surviving sampling is held in memory as a Python dict during this
      pass (bounded by distinct-account cardinality among sampled rows,
      not full-dataset row count — see MEMORY NOTES below).

  Pass 2 (fact load):
      Re-read dim_account from Postgres into an in-memory dict
      (account_id -> account_key), then stream the CSV a second time,
      independently re-apply the identical sampling filter, resolve both
      FK columns via that dict, and bulk-insert into `fact_transactions`
      via COPY in chunks.

Two linear passes over a 470MB file is a deliberate, cheap trade: it avoids
having to buffer fact rows with unresolved forward-references (a row's
nameDest may not appear as a nameOrig until later in the file) within a
single pass. Each pass is I/O-bound and fast; the complexity avoided is
worth more than the second scan costs.

WHY COPY, NOT executemany/INSERT
---------------------------------
At 6.36M rows, COPY (via psycopg2's copy_expert with an in-memory CSV
buffer) is 10-50x faster than batched parameterized INSERTs. This script
uses synchronous psycopg2 deliberately — a one-shot batch ETL has no
concurrency to exploit, so async (asyncpg, used elsewhere in this project's
FastAPI layer) would add complexity for zero benefit here.

MEMORY NOTES
------------
- Pass 1 holds a Python set of distinct account IDs in memory. PaySim's
  worst-case distinct-account count is bounded by 2 * row_count (~12.7M),
  but realistically far lower since destinations repeat heavily (merchants
  in particular). Tested footprint is documented in findings/ once run.
- Pass 2 holds the full account_id -> account_key dict in memory (one
  dict entry per distinct account, not per row) — this is what makes
  per-row FK resolution O(1) without hitting the database per row.
- The CSV itself is streamed in chunks (CHUNK_SIZE rows at a time via
  pandas.read_csv(chunksize=...)) in both passes — never loaded whole.

IDEMPOTENCY
-----------
Run with --reset to truncate all three tables (respecting FK order) before
reloading. Without --reset, the script checks existing row counts and
refuses to double-load into a non-empty fact_transactions, so re-running
after a crash doesn't silently duplicate data.

STORAGE MONITORING
-------------------
Every COMMIT_EVERY chunks, the script queries pg_database_size() and logs
it against the known free-tier ~500MB cap (Supabase/Neon), so a storage
overrun is visible during the load rather than discovered after.

USAGE
-----
    python load_data.py --csv data/raw/paysim.csv
    python load_data.py --csv data/raw/paysim.csv --reset
    python load_data.py --csv data/raw/paysim.csv --limit 1000000   # smoke test
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import pandas as pd
import psycopg2
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

CHUNK_SIZE = 100_000          # rows per pandas read_csv chunk
COMMIT_EVERY_N_CHUNKS = 1      # commit + size-check cadence during pass 2
FREE_TIER_STORAGE_CAP_MB = 500  # Supabase/Neon free-tier ballpark cap

# --- Stratified sampling configuration ---------------------------------
# Free-tier Postgres (Supabase/Neon) enforces a ~500MB database-size quota
# that triggers read-only mode once exceeded — confirmed empirically: a
# prior full-load attempt hit this at well under the full 6.36M rows.
#
# Strategy: keep 100% of fraud rows (rare, ~0.13% of the dataset, and the
# entire analytical point of this project) and deterministically sample
# non-fraud rows down to a row budget that fits comfortably under the cap.
#
# Sample rate derived from an empirical measurement: 100,000 fact rows plus
# their proportional dim_account/index overhead measured at 48.4MB, giving
# ~484 bytes/row all-in. Target 400MB (80% of the 500MB cap, leaving
# headroom for materialized views built later) / 484 bytes/row ≈ 858,000
# total rows. Fraud rows (8,213 in the full PaySim dataset) are kept in
# full; the remainder of the budget (~850,000 rows) is filled by sampling
# non-fraud rows at a fixed, reproducible rate.
#
# NON_FRAUD_SAMPLE_RATE is set so that, in expectation, sampled non-fraud
# rows ≈ TARGET_NON_FRAUD_ROWS out of ~6,354,407 total non-fraud rows in
# the full dataset (6,362,620 - 8,213 fraud rows).
TARGET_NON_FRAUD_ROWS = 850_000
FULL_DATASET_NON_FRAUD_ROWS = 6_354_407  # 6,362,620 total - 8,213 fraud
NON_FRAUD_SAMPLE_RATE = TARGET_NON_FRAUD_ROWS / FULL_DATASET_NON_FRAUD_ROWS

SAMPLING_SEED = 42  # fixed seed: sampling decisions are reproducible run-to-run

EXPECTED_COLUMNS = [
    "step", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig",
    "nameDest", "oldbalanceDest", "newbalanceDest", "isFraud", "isFlaggedFraud",
]

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
log = logging.getLogger("load_data")


class LoadDataError(RuntimeError):
    """Raised for any condition that should stop the load immediately."""


def should_include_row(is_fraud: bool, row_hash_input: str) -> bool:
    """
    Deterministic stratified-sampling decision for a single CSV row.

    - Fraud rows: always included (100% retention).
    - Non-fraud rows: included based on a stable hash of row-identifying
      fields, compared against NON_FRAUD_SAMPLE_RATE. Using a hash (not
      pandas row position or a per-row RNG draw) guarantees:
        1. The same row gets the same inclusion decision in pass 1 and
           pass 2, even though they're separate read_csv() invocations.
        2. The decision is reproducible across machines/runs (no reliance
           on iteration order or random-state continuity across chunks).
        3. --limit smoke tests sample consistently with full runs.

    NOTE: row_hash_input must be built from fields that are unique enough
    to avoid systematic bias (e.g. don't hash on `type` alone, which only
    has 5 values and would make sampling correlate with transaction type).
    """
    if is_fraud:
        return True
    # md5 chosen over Python's built-in hash() deliberately: hash() is
    # randomized per-process (PYTHONHASHSEED) unless explicitly disabled,
    # which would break reproducibility across the two passes/runs. md5
    # is stable and fast enough at this scale (not used for security here).
    digest = hashlib.md5(row_hash_input.encode("utf-8")).hexdigest()
    # Use the first 8 hex chars as a uniform 32-bit integer threshold check.
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return bucket < NON_FRAUD_SAMPLE_RATE


@dataclass
class LoadStats:
    rows_seen: int = 0
    rows_sampled_in: int = 0
    distinct_accounts: int = 0
    fact_rows_loaded: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at


# -----------------------------------------------------------------------------
# DB connection
# -----------------------------------------------------------------------------

def get_connection():
    """
    Open a psycopg2 connection using DATABASE_URL_DIRECT from the environment.

    Deliberately uses the "direct" (session-mode pooler, in this project's
    case) connection string, not the transaction pooler — bulk COPY and
    multi-statement transactional loads belong on a session-style connection,
    consistent with how schema.sql was applied.
    """
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL_DIRECT")
    if not dsn:
        raise LoadDataError(
            "DATABASE_URL_DIRECT is not set. Check that .env exists and "
            "has been populated (see .env.example for the expected format)."
        )
    try:
        conn = psycopg2.connect(dsn)
    except psycopg2.OperationalError as exc:
        raise LoadDataError(f"Could not connect to database: {exc}") from exc
    conn.autocommit = False
    return conn


# -----------------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------------

def validate_csv_header(csv_path: str) -> None:
    """
    Read only the header row and fail fast if columns don't match what the
    rest of this script assumes. Catches a wrong/corrupted CSV before any
    expensive work happens, rather than failing confusingly mid-load.
    """
    header_df = pd.read_csv(csv_path, nrows=0)
    actual_columns = list(header_df.columns)
    if actual_columns != EXPECTED_COLUMNS:
        raise LoadDataError(
            "CSV header does not match expected PaySim schema.\n"
            f"  Expected: {EXPECTED_COLUMNS}\n"
            f"  Actual:   {actual_columns}\n"
            "Refusing to proceed — column order/casing mismatch will "
            "silently corrupt the load if not caught here."
        )
    log.info("CSV header validated: %d columns match expected schema.", len(actual_columns))


def check_existing_data(conn, reset: bool) -> None:
    """
    Refuse to load into a non-empty fact_transactions unless --reset was
    passed. Prevents silent duplication from re-running after a crash.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fact_transactions;")
        existing = cur.fetchone()[0]

    if existing == 0:
        return

    if not reset:
        raise LoadDataError(
            f"fact_transactions already has {existing:,} rows. "
            "Re-run with --reset to truncate and reload, or this is "
            "intentional and you should investigate before proceeding."
        )

    log.warning("Existing %s rows found. --reset passed: truncating all tables.", f"{existing:,}")
    with conn.cursor() as cur:
        # CASCADE not needed since FK order is explicit and deliberate here;
        # truncate fact first (it references dim_account), then dimensions.
        cur.execute("TRUNCATE TABLE fact_transactions;")
        cur.execute("TRUNCATE TABLE dim_account RESTART IDENTITY CASCADE;")
        # dim_transaction_type is reference data seeded by schema.sql — never
        # truncated here; it's not something this loader owns.
    conn.commit()
    log.info("Truncation complete.")


def log_database_size(conn) -> float:
    """Query current database size in MB and log it against the free-tier cap."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database());")
        size_bytes = cur.fetchone()[0]
    size_mb = size_bytes / (1024 * 1024)
    pct = (size_mb / FREE_TIER_STORAGE_CAP_MB) * 100
    log.info(
        "Current database size: %.1f MB (%.0f%% of %d MB free-tier cap)",
        size_mb, pct, FREE_TIER_STORAGE_CAP_MB,
    )
    if pct >= 90:
        log.warning(
            "Database size is at %.0f%% of the free-tier cap. "
            "Consider stopping and reassessing load strategy (sampling, "
            "dropping non-critical indexes, or upgrading tier).",
            pct,
        )
    return size_mb


# -----------------------------------------------------------------------------
# Pass 1: build dim_account
# -----------------------------------------------------------------------------

def derive_account_type(account_id: str) -> str:
    """
    PaySim convention: 'C' prefix = customer, 'M' prefix = merchant.
    Fails loudly on anything else rather than guessing — an unexpected
    prefix means either a corrupted row or an assumption that no longer
    holds, and silently defaulting would corrupt dim_account's type column.
    """
    if account_id.startswith("C"):
        return "CUSTOMER"
    if account_id.startswith("M"):
        return "MERCHANT"
    raise LoadDataError(
        f"Unexpected account ID prefix in '{account_id}' — expected 'C' or "
        "'M'. This indicates a non-standard PaySim CSV; stopping rather "
        "than guessing the account_type."
    )


def _copy_account_batch(conn, batch: list[tuple[str, str]]) -> None:
    """COPY a single batch of (account_id, account_type) rows into dim_account."""
    buf = io.StringIO()
    for account_id, account_type in batch:
        buf.write(f"{account_id}\t{account_type}\n")
    buf.seek(0)
    with conn.cursor() as cur:
        cur.copy_expert(
            "COPY dim_account (account_id, account_type) FROM STDIN WITH (FORMAT text)",
            buf,
        )
    conn.commit()


def build_dim_account(conn, csv_path: str, limit: int | None, stats: LoadStats) -> None:
    """
    Pass 1: stream the CSV, apply the SAME stratified-sampling filter that
    pass 2 will apply, and bulk-load only the accounts that survive
    sampling into dim_account via COPY.

    Sampling must be applied here too — not just in pass 2 — otherwise
    dim_account would retain accounts that only ever appeared in rows
    pass 2 discards, defeating the point of sampling down to fit the
    storage budget.

    The COPY itself is batched (not one single multi-million-row
    statement) because managed Postgres providers (Supabase/Neon free
    tier, via their PgBouncer pooler) enforce a server-side
    statement_timeout. A single COPY covering all distinct accounts in
    one transaction can exceed that timeout even though the data volume
    itself is well within what Postgres can handle — this is a
    pooler/timeout constraint, not a data-volume constraint, and batching
    with periodic commits is the correct fix rather than trying to raise
    the timeout (which isn't configurable on free-tier poolers anyway).
    """
    log.info(
        "Pass 1/2: scanning for distinct accounts (stratified sample: "
        "100%% fraud + %.2f%% non-fraud)...",
        NON_FRAUD_SAMPLE_RATE * 100,
    )
    seen: dict[str, str] = {}  # account_id -> account_type

    needed_cols = ["nameOrig", "nameDest", "isFraud", "step", "amount"]
    rows_read = 0
    rows_sampled_in = 0
    for chunk in pd.read_csv(csv_path, usecols=needed_cols, chunksize=CHUNK_SIZE):
        if limit and rows_read >= limit:
            break

        remaining = (limit - rows_read) if limit else None
        if remaining is not None and remaining < len(chunk):
            chunk = chunk.iloc[:remaining]

        for row in chunk.itertuples(index=False):
            hash_input = f"{row.nameOrig}|{row.nameDest}|{row.step}|{row.amount}"
            if not should_include_row(bool(row.isFraud), hash_input):
                continue
            rows_sampled_in += 1
            if row.nameOrig not in seen:
                seen[row.nameOrig] = derive_account_type(row.nameOrig)
            if row.nameDest not in seen:
                seen[row.nameDest] = derive_account_type(row.nameDest)

        rows_read += len(chunk)

    stats.distinct_accounts = len(seen)
    stats.rows_sampled_in = rows_sampled_in
    log.info(
        "Pass 1 scan complete: %s rows scanned, %s rows sampled in (%.2f%% "
        "of scanned), %s distinct accounts found.",
        f"{rows_read:,}", f"{rows_sampled_in:,}",
        100 * rows_sampled_in / rows_read if rows_read else 0,
        f"{len(seen):,}",
    )

    # Bulk-load via COPY, batched to stay under the pooler's statement_timeout.
    # Never issues a per-row INSERT — each batch is still a single COPY.
    items = list(seen.items())
    total = len(items)
    loaded = 0

    for start in range(0, total, CHUNK_SIZE):
        batch = items[start : start + CHUNK_SIZE]
        _copy_account_batch(conn, batch)
        loaded += len(batch)
        log.info("dim_account load progress: %s / %s accounts", f"{loaded:,}", f"{total:,}")

    log.info("Pass 1 load complete: %s accounts inserted into dim_account.", f"{total:,}")


def load_account_key_map(conn) -> dict[str, int]:
    """Pull the full account_id -> account_key mapping into memory for pass 2."""
    with conn.cursor() as cur:
        cur.execute("SELECT account_id, account_key FROM dim_account;")
        mapping = dict(cur.fetchall())
    log.info("Loaded %s account key mappings for pass 2.", f"{len(mapping):,}")
    return mapping


def load_transaction_type_key_map(conn) -> dict[str, int]:
    """Pull type_name -> transaction_type_key (5 rows, seeded by schema.sql)."""
    with conn.cursor() as cur:
        cur.execute("SELECT type_name, transaction_type_key FROM dim_transaction_type;")
        mapping = dict(cur.fetchall())
    if len(mapping) != 5:
        raise LoadDataError(
            f"Expected 5 transaction types in dim_transaction_type, found "
            f"{len(mapping)}. Was schema.sql applied correctly?"
        )
    return mapping


# -----------------------------------------------------------------------------
# Pass 2: build fact_transactions
# -----------------------------------------------------------------------------

FACT_COLUMNS = [
    "step", "transaction_type_key", "amount",
    "orig_account_key", "old_balance_orig", "new_balance_orig",
    "dest_account_key", "old_balance_dest", "new_balance_dest",
    "is_fraud", "is_flagged_fraud",
]


def build_fact_transactions(
    conn,
    csv_path: str,
    account_keys: dict[str, int],
    type_keys: dict[str, int],
    limit: int | None,
    stats: LoadStats,
) -> None:
    """
    Pass 2: stream the CSV again, apply the same stratified-sampling filter
    used in pass 1, resolve FKs via the in-memory maps built above, and
    bulk-load fact_transactions via COPY in chunks.

    NOTE on --limit semantics: --limit caps the number of *source CSV rows
    scanned*, not the number of rows that survive sampling. This keeps
    --limit's meaning identical between pass 1 and pass 2 (both scan the
    same prefix of the file) — a small --limit smoke test will scan few
    rows and may sample in very few of them, which is fine for verifying
    plumbing but not representative of full-run fraud rates.
    """
    log.info("Pass 2/2: loading fact_transactions (applying stratified sample)...")

    chunks_since_commit = 0
    rows_scanned = 0
    rows_loaded = 0

    for chunk_num, chunk in enumerate(pd.read_csv(csv_path, chunksize=CHUNK_SIZE), start=1):
        if limit and rows_scanned >= limit:
            break

        remaining = (limit - rows_scanned) if limit else None
        if remaining is not None and remaining < len(chunk):
            chunk = chunk.iloc[:remaining]

        buf = io.StringIO()
        rows_in_chunk = 0
        for row in chunk.itertuples(index=False):
            hash_input = f"{row.nameOrig}|{row.nameDest}|{row.step}|{row.amount}"
            if not should_include_row(bool(row.isFraud), hash_input):
                continue

            try:
                orig_key = account_keys[row.nameOrig]
                dest_key = account_keys[row.nameDest]
                type_key = type_keys[row.type]
            except KeyError as exc:
                raise LoadDataError(
                    f"Unresolved FK at CSV row ~{stats.rows_seen}: {exc}. "
                    "This means pass 1 did not capture every account/type "
                    "seen in pass 2 — the two passes are reading different "
                    "data, which should not happen against a static file."
                ) from exc

            buf.write(
                "\t".join([
                    str(row.step),
                    str(type_key),
                    f"{row.amount:.2f}",
                    str(orig_key),
                    f"{row.oldbalanceOrg:.2f}",
                    f"{row.newbalanceOrig:.2f}",
                    str(dest_key),
                    f"{row.oldbalanceDest:.2f}",
                    f"{row.newbalanceDest:.2f}",
                    "t" if row.isFraud else "f",
                    "t" if row.isFlaggedFraud else "f",
                ]) + "\n"
            )
            rows_in_chunk += 1
            stats.rows_seen += 1

        rows_scanned += len(chunk)

        if rows_in_chunk == 0:
            # Entire chunk was sampled out (common at a ~13% non-fraud rate
            # with no fraud rows in this particular chunk) — skip the COPY
            # call rather than issuing a no-op round-trip.
            continue

        buf.seek(0)
        with conn.cursor() as cur:
            cur.copy_expert(
                f"COPY fact_transactions ({', '.join(FACT_COLUMNS)}) "
                "FROM STDIN WITH (FORMAT text)",
                buf,
            )

        rows_loaded += rows_in_chunk
        stats.fact_rows_loaded = rows_loaded
        chunks_since_commit += 1

        if chunks_since_commit >= COMMIT_EVERY_N_CHUNKS:
            conn.commit()
            chunks_since_commit = 0
            elapsed = stats.elapsed()
            rate = rows_loaded / elapsed if elapsed > 0 else 0
            log.info(
                "Chunk %d: %s rows scanned, %s rows loaded (%.0f rows/sec, %.1fs elapsed)",
                chunk_num, f"{rows_scanned:,}", f"{rows_loaded:,}", rate, elapsed,
            )
            log_database_size(conn)

    conn.commit()
    log.info("Pass 2 complete: %s fact rows loaded in %.1fs.", f"{rows_loaded:,}", stats.elapsed())


# -----------------------------------------------------------------------------
# Post-load verification
# -----------------------------------------------------------------------------

def verify_load(conn, expected_min_rows: int) -> None:
    """Sanity-check row counts and referential integrity after load."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fact_transactions;")
        fact_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM dim_account;")
        account_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM fact_transactions f "
            "LEFT JOIN dim_account a ON f.orig_account_key = a.account_key "
            "WHERE a.account_key IS NULL;"
        )
        orphaned_orig = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM fact_transactions WHERE is_fraud = TRUE;")
        fraud_count = cur.fetchone()[0]

    log.info("--- Post-load verification ---")
    log.info("fact_transactions: %s rows", f"{fact_count:,}")
    log.info("dim_account: %s rows", f"{account_count:,}")
    log.info("fraud rows: %s (%.3f%%)", f"{fraud_count:,}", 100 * fraud_count / fact_count if fact_count else 0)
    log.info("orphaned orig FKs: %d (must be 0)", orphaned_orig)

    if orphaned_orig > 0:
        raise LoadDataError(
            f"{orphaned_orig} fact rows have an orig_account_key with no "
            "matching dim_account row. This should be impossible given "
            "the two-pass design — investigate immediately."
        )

    if fact_count < expected_min_rows:
        log.warning(
            "Loaded %s rows, fewer than the expected %s. If this was a "
            "--limit smoke test, this is expected; otherwise investigate.",
            f"{fact_count:,}", f"{expected_min_rows:,}",
        )

    log.info("Verification passed.")


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Path to the PaySim CSV file.")
    parser.add_argument(
        "--reset", action="store_true",
        help="Truncate fact_transactions and dim_account before loading.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Load only the first N rows (smoke testing). Omit for full load.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        log.error("CSV file not found: %s", args.csv)
        return 1

    stats = LoadStats()
    conn = None
    try:
        validate_csv_header(args.csv)

        conn = get_connection()
        check_existing_data(conn, reset=args.reset)

        build_dim_account(conn, args.csv, args.limit, stats)

        account_keys = load_account_key_map(conn)
        type_keys = load_transaction_type_key_map(conn)

        build_fact_transactions(conn, args.csv, account_keys, type_keys, args.limit, stats)

        # Expected count reflects the stratified-sampled target, not the
        # full 6,362,620-row dataset — sampling is now the default behavior
        # given the free-tier storage constraint. ~8,213 fraud rows (100%
        # retained) + ~850,000 sampled non-fraud rows ≈ 858,213 total.
        # A --limit smoke test will load far fewer; only warn (not fail)
        # if under this target either way, since exact sampled counts vary
        # by a few percent run-to-run on different CSV slices via --limit.
        expected_rows = args.limit if args.limit else (8_213 + TARGET_NON_FRAUD_ROWS)
        verify_load(conn, expected_min_rows=expected_rows)

        log.info(
            "Load finished successfully: %s fact rows, %s distinct accounts, %.1fs total.",
            f"{stats.fact_rows_loaded:,}", f"{stats.distinct_accounts:,}", stats.elapsed(),
        )
        return 0

    except LoadDataError as exc:
        log.error("Load aborted: %s", exc)
        if conn is not None:
            conn.rollback()
        return 1

    except Exception:
        log.exception("Unexpected error during load.")
        if conn is not None:
            conn.rollback()
        return 1

    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())