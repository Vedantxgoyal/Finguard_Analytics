"""
database.py — Async SQLAlchemy engine/session setup for the FinGuard
Analytics FastAPI backend.

CONNECTION TARGET: DATABASE_URL_POOLED, NOT DATABASE_URL_DIRECT
------------------------------------------------------------------
This app connects through Supabase's transaction-mode pooler (Supavisor,
port 6543), not the session-mode direct connection used by db/load_data.py
and db/refresh_views.py. This split is deliberate:

  - DATABASE_URL_DIRECT (session mode): used by one-shot admin scripts
    (schema application, bulk load, materialized view refresh) that need
    full session semantics (multi-statement transactions, DDL, the
    REFRESH ... CONCURRENTLY pattern) and run infrequently.
  - DATABASE_URL_POOLED (transaction mode): used by this FastAPI app,
    which makes many short-lived queries from potentially many concurrent
    requests — exactly the workload transaction-mode pooling is designed
    for. Each query gets a pooled connection for the duration of its
    transaction only, then releases it back to the pool immediately.

Transaction mode has real constraints worth knowing if this app grows:
no session-level state (SET, prepared statements pre-PgBouncer 1.21,
LISTEN/NOTIFY, advisory locks tied to a session) survives between
queries, since two queries in the "same" app-level connection may
physically land on different backend Postgres connections. This app's
query patterns (stateless filtered reads against materialized views) do
not rely on any of that, so transaction mode is the right choice without
caveats for this specific workload.

POOL SIZING
-----------
SQLAlchemy's own connection pool sits ON TOP OF Supavisor's pool — this is
double-pooling, which is correct and expected (it's the standard
client-library-pool + server-side-pool pattern), but it means this app's
pool_size should stay small. Supabase's free-tier `nano` compute caps
total pooler connections low; per Supabase's own guidance, an app should
generally stay well under the instance's connection ceiling to leave
headroom for other concurrent users of the same database (the SQL
Editor, manual psql sessions, admin scripts). pool_size=5 + max_overflow=5
caps this app at 10 concurrent backend connections at absolute peak,
which is conservative relative to the free-tier nano ceiling and leaves
room for everything else touching this database during development.

SESSION-PER-REQUEST PATTERN
-----------------------------
get_db() is a FastAPI dependency (via Depends) that yields one
AsyncSession per request and guarantees it's closed afterward, including
on exception. Routers should depend on this rather than importing a
module-level session directly — sharing one session across concurrent
requests is a correctness bug (SQLAlchemy AsyncSession is not safe for
concurrent use from multiple coroutines).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def _build_database_url() -> str:
    """
    Read DATABASE_URL_POOLED from the environment and adapt it for
    SQLAlchemy's async engine.

    Two transformations are required, not optional:
      1. SQLAlchemy's asyncpg dialect needs the `postgresql+asyncpg://`
         scheme, not bare `postgresql://` — asyncpg is not the default
         driver SQLAlchemy picks for a bare postgresql:// URL.
      2. The `?pgbouncer=true` query parameter (present in this project's
         .env.example for the pooled connection string) is a hint some
         tools use, but asyncpg's connect() does not accept it as a
         keyword argument and will raise on an unrecognized parameter —
         it must be stripped here, with the actual prepared-statement-
         disabling behavior it implies handled instead via
         `statement_cache_size=0` in connect_args (see get_engine()).
    """
    raw_url = os.environ.get("DATABASE_URL_POOLED")
    if not raw_url:
        raise RuntimeError(
            "DATABASE_URL_POOLED is not set. Check that .env exists and "
            "has been populated (see .env.example for the expected format)."
        )

    url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Strip ?pgbouncer=true (and any other query string) — asyncpg's
    # connect() rejects unrecognized parameters passed this way.
    if "?" in url:
        url = url.split("?", 1)[0]

    return url


# Engine is created once at import time (module-level singleton) and
# reused for the app's lifetime — creating a new engine per request would
# defeat the entire point of connection pooling.
_engine = create_async_engine(
    _build_database_url(),
    pool_size=5,
    max_overflow=5,
    pool_timeout=10,
    pool_pre_ping=True,
    # PgBouncer in transaction mode does not support server-side prepared
    # statements reliably across all configurations — disabling asyncpg's
    # statement cache avoids "prepared statement does not exist" errors
    # that can otherwise surface intermittently under transaction pooling.
    connect_args={"statement_cache_size": 0},
    echo=False,  # set True locally for SQL query debugging, never in prod
)

_SessionLocal = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency yielding one AsyncSession per request.

    Usage in a router:

        from fastapi import Depends
        from backend.database import get_db

        @router.get("/something")
        async def get_something(db: AsyncSession = Depends(get_db)):
            result = await db.execute(...)
            ...

    The session is always closed on the way out, including when the
    request handler raises — `async with` on the sessionmaker's context
    manager guarantees this without needing an explicit try/finally here.
    """
    async with _SessionLocal() as session:
        yield session


@asynccontextmanager
async def lifespan_db_check() -> AsyncIterator[None]:
    """
    Intended for use as (part of) main.py's FastAPI lifespan context:
    verifies the database is reachable at startup, failing fast with a
    clear error rather than letting the app start and only discover a
    broken connection on the first real request.

    Usage in main.py:

        from contextlib import asynccontextmanager
        from backend.database import lifespan_db_check, dispose_engine

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with lifespan_db_check():
                yield
            await dispose_engine()
    """
    from sqlalchemy import text

    async with _engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    yield


async def dispose_engine() -> None:
    """
    Cleanly dispose of the engine's connection pool. Call this on app
    shutdown (see lifespan_db_check usage example above) so pooled
    connections are released back to Supavisor explicitly rather than
    left to time out, which matters on a free-tier instance with a low
    total connection ceiling shared across however many things are
    touching this database during development.
    """
    await _engine.dispose()