"""
main.py — FastAPI application entrypoint for FinGuard Analytics.

Run locally:
    uvicorn backend.main:app --reload --port 8000

Run in production (no --reload, the deploy/backend.dockerfile CMD does this):
    uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

# MUST run before importing backend.database: that module reads
# DATABASE_URL_POOLED from os.environ at IMPORT TIME (module-level engine
# construction), not lazily inside a function. Under `uvicorn --reload`,
# the actual app runs in a spawned reloader subprocess that does NOT
# inherit .env values loaded by some other Python process (e.g. a
# separate manual smoke-test script run beforehand in the same shell) —
# only real OS-level environment variables survive into that subprocess.
# Calling load_dotenv() here, as the first thing this module does,
# guarantees .env is read directly inside whichever process actually
# imports and runs this app, reload subprocess or not.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import dispose_engine, lifespan_db_check
from backend.routers import fraud_patterns, overview, velocity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("finguard")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup: verify the database is reachable before accepting traffic —
    fails fast with a clear log line rather than letting the app boot
    successfully and only discover a broken DB connection on the first
    real request (which would surface as an opaque 500 to whoever hits
    it first, including potentially an interviewer clicking the live
    demo link).

    Shutdown: explicitly dispose of the SQLAlchemy engine's connection
    pool, releasing pooled connections back to Supabase's Supavisor
    rather than leaving them to time out — relevant given the free-tier
    instance's low total connection ceiling is shared with whatever else
    (psql sessions, the SQL Editor, admin scripts) might be touching the
    same database during development.
    """
    log.info("Starting up: verifying database connectivity...")
    try:
        async with lifespan_db_check():
            log.info("Database connectivity verified.")
            yield
    finally:
        log.info("Shutting down: disposing database engine...")
        await dispose_engine()
        log.info("Shutdown complete.")


app = FastAPI(
    title="FinGuard Analytics API",
    description=(
        "Fraud and transaction analytics over the PaySim dataset. All "
        "endpoints read from precomputed materialized views — see "
        "db/materialized_views.sql — never the raw fact table directly."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------
# Origins are read from an environment variable rather than hardcoded,
# since the frontend's deployed URL (Vercel) isn't known at the time this
# file is written and will differ between local dev and production. A
# comma-separated list in ALLOWED_ORIGINS covers both cases from one
# source of truth instead of an if/else on an environment flag.
#
# SECURITY NOTE: allow_origins=["*"] is deliberately NOT used here, even
# though this is a portfolio project with no real user data at stake —
# wildcard CORS alongside allow_credentials=True is rejected by browsers
# anyway, and explicit origins is the correct production pattern to
# demonstrate regardless of this project's actual risk level.
_allowed_origins_raw = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173"
)
ALLOWED_ORIGINS = [origin.strip() for origin in _allowed_origins_raw.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],  # this API is read-only; no POST/PUT/DELETE exist yet
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Routers
# -----------------------------------------------------------------------------
app.include_router(overview.router)
app.include_router(fraud_patterns.router)
app.include_router(velocity.router)


# -----------------------------------------------------------------------------
# Health check
# -----------------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """
    Liveness check for the deploy platform (Render/Railway) and for
    manual verification after deploy. Deliberately does NOT touch the
    database — that's what the startup lifespan check is for. A health
    check that queries the DB on every call adds load for no benefit;
    if the DB were down, the app would have failed to start in the
    first place under the lifespan policy above.
    """
    return {"status": "ok"}


@app.get("/", tags=["health"])
async def root() -> dict[str, str]:
    """Root endpoint — points humans toward the interactive API docs."""
    return {
        "service": "FinGuard Analytics API",
        "docs": "/docs",
    }