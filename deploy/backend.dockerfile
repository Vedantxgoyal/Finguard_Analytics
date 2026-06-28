# =============================================================================
# FinGuard Analytics — Backend Dockerfile
# =============================================================================
# Multi-stage build: a "builder" stage installs Python dependencies
# (including compiling asyncpg's C extensions, which need gcc/build
# headers not needed at runtime), and a slim "runtime" stage copies only
# the installed packages + application code into a clean base image.
#
# WHY MULTI-STAGE: a single-stage build that runs `apt-get install
# build-essential && pip install` and ships that same image to production
# carries the entire C compiler toolchain into the deployed container —
# unnecessary attack surface and meaningfully larger image size (slower
# cold starts/pulls on Render/Railway's free tier, which matters given
# this project's other free-tier constraints). The builder stage's layers
# are discarded entirely; only its installed site-packages get copied
# forward.
#
# Build and run locally:
#   docker build -f deploy/backend.dockerfile -t finguard-backend .
#   docker run --env-file .env -p 8000:8000 finguard-backend
#
# (Build context is the repo ROOT, not deploy/, because this Dockerfile
# COPYs backend/ — run docker build from the repo root with -f pointing
# at this file, as shown above, not `cd deploy && docker build .`)
# =============================================================================


# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# build-essential provides gcc, needed to compile asyncpg's C extension
# during pip install. This entire layer is discarded after this stage —
# it never reaches the final image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only requirements.txt first (not the full app) so Docker's layer
# cache is invalidated by dependency changes, not by every application
# code change — a code-only change re-uses this cached pip-install layer
# entirely, making rebuilds during development meaningfully faster.
COPY backend/requirements.txt .

# --user installs into /root/.local rather than system site-packages,
# making the "copy only what's needed" step in the runtime stage a single
# clean directory copy rather than having to cherry-pick specific paths
# out of a system-wide install that also contains apt-installed packages.
RUN pip install --no-cache-dir --user -r requirements.txt


# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root user: running the app as root inside the container is an
# avoidable privilege escalation surface with zero benefit here — this
# app needs no special OS-level privileges (it only opens an outbound
# DB connection and listens on a port, neither requires root).
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy the installed packages from the builder stage's --user install
# location. This is the ONLY thing carried over from the builder stage —
# no compiler, no build headers, no apt package cache.
COPY --from=builder /root/.local /home/appuser/.local

# Copy application code. Done AFTER the dependency layer (above) so that
# code-only changes don't invalidate the (expensive) dependency-install
# layer cache on rebuild.
COPY backend/ ./backend/

# Make the --user-installed packages (now under appuser's home) importable
# and put their console scripts (uvicorn's entry point) on PATH.
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/home/appuser/.local/lib/python3.12/site-packages \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Hand ownership of the app directory to the non-root user before
# switching to it — appuser needs to be able to read these files.
RUN chown -R appuser:appuser /app /home/appuser/.local

USER appuser

# Render/Railway both set $PORT at runtime and expect the container to
# bind to it — hardcoding 8000 here would break on either platform if
# they assign a different port. The shell form of CMD is required (not
# the exec-array form) specifically so that $PORT is expanded by the
# shell before uvicorn sees it; the exec-array form does NOT perform
# environment variable substitution.
#
# EXPOSE 8000 is documentation only (it does not actually publish the
# port or affect runtime behavior) — included for local `docker run -p`
# clarity; the real bound port at deploy time comes from $PORT below.
EXPOSE 8000

CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
