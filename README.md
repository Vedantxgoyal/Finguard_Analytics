# FinGuard Analytics

A fraud and transaction analytics platform built on the [PaySim](https://www.kaggle.com/datasets/ealaxi/paysim1) synthetic mobile-money dataset (6.36M transactions) — PostgreSQL star schema with materialized views, a FastAPI backend, and a dark-terminal-style Next.js dashboard for live fraud-pattern exploration.

This is a Postgres-heavy, engineering-heavy analytics project — **no machine learning**. The fraud findings below come from SQL aggregation against precomputed materialized views, not a trained model. The interesting engineering decisions are about making that work cleanly under a real infrastructure constraint (see [Storage Constraint](#the-storage-constraint--why-this-is-a-sample) below), and the interesting analytical decisions are about what the data actually shows once you can query it fast.

---

## What's actually in here

```
db/                  Star schema, stratified bulk loader, materialized views, refresh script
backend/              FastAPI app — 3 routers, async SQLAlchemy + asyncpg, Pydantic schemas
frontend/             Next.js 16 dashboard — dark fintech-terminal UI, 3 pages
findings/             Analysis report with every figure traced back to a real query
deploy/               Multi-stage production Dockerfile for the backend
```

## Architecture

```
PaySim CSV (6.36M rows)
        │
        ▼ stratified sampling load (db/load_data.py)
PostgreSQL (Supabase) ── star schema: fact_transactions + dim_account + dim_transaction_type
        │
        ▼ db/materialized_views.sql
3 materialized views ── hourly fraud summary · segment fraud rates · account velocity
        │
        ▼ async SQLAlchemy / asyncpg
FastAPI backend ── 3 routers, each backed by exactly one materialized view
        │
        ▼ same-origin proxy routes (Next.js API routes)
Next.js frontend ── dark terminal dashboard, 3 pages
```

**The frontend never queries `fact_transactions` directly, on any page.** Every chart, table, and heatmap cell reads from a precomputed materialized view. This is the core architectural decision of the project, and it's a real production pattern, not a workaround: ad-hoc aggregate queries against an 858K-row fact table on every filter interaction would be the wrong design even with unlimited infrastructure — precomputing the aggregates that the UI actually needs, and refreshing them on a schedule, is what a real analytics platform does at any scale.

## The storage constraint — why this is a sample

Free-tier managed Postgres enforces a hard ~500MB database-size quota — confirmed empirically during this build, not from documentation: loading the full 6.36M-row dataset plus indexing pushed the database into **read-only mode** mid-load. Rather than switch to a smaller, less interesting dataset, the load was redesigned around **stratified sampling**:

- **100% of fraud transactions retained** — all 8,213 fraud rows from the full dataset are present.
- **Non-fraud transactions sampled at a fixed ~13.4% rate** — deterministic (seeded hash of stable row fields), reproducible across runs, sized from an empirical per-row storage measurement to fit comfortably under the cap.

Result: **858,573 transactions**, **491MB total database size**, **7.41x fraud enrichment** over the natural ~0.13% baseline rate. Full methodology and rationale: [`findings/findings_report.md`](findings/findings_report.md).

## Key findings (real numbers, not illustrative)

- **Fraud is structurally confined to two transaction types** — `TRANSFER` and `CASH_OUT`. `PAYMENT`, `CASH_IN`, and `DEBIT` carry zero fraud across 480,214 sampled transactions of those types.
- **`CASH_OUT` transactions over $1M have a 97.32% fraud rate** — 1,345 of 1,382 such transactions are fraudulent. This single segment, 0.16% of all transactions, accounts for 43% of all fraud dollar value in the dataset.
- **PaySim's own `isFlaggedFraud` detection rule misses 99.81% of actual fraud** — it flags only 16 of 8,213 fraud cases, with zero false positives. Perfect precision, near-zero recall — a useful cautionary example of what not to ship as a complete detection system.
- **The dataset's highest-value fraud cases are single, isolated transactions**, not sustained mule-account activity — the top 10 highest-value fraud accounts each have exactly 1 transaction, 1 counterparty, and a $10,000,000 amount. Velocity-based detection alone would miss every one of them.

Full writeup with every figure traced to a reproducible query: [`findings/findings_report.md`](findings/findings_report.md).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Database | PostgreSQL (Supabase) | Star schema, materialized views, partial/composite indexes |
| Backend | FastAPI + SQLAlchemy (async) + asyncpg | Async I/O for a request-serving API; parameterized queries only |
| Frontend | Next.js 16 (App Router) + TypeScript + Tailwind | Server components for initial data fetch, client components for interactive filters |
| Charts | Recharts | Composes with React's render cycle for live-filtering interactions |
| Containerization | Docker (multi-stage build) | `deploy/backend.dockerfile` — builder stage compiles deps, slim runtime ships only what's needed |

## Running it locally

**1. Database** — already-loaded Supabase instance, or point at your own:
```bash
psql $DATABASE_URL_DIRECT -f db/schema.sql
psql $DATABASE_URL_DIRECT -f db/materialized_views.sql
python db/load_data.py --csv data/raw/paysim.csv --reset
python db/refresh_views.py
```

**2. Backend:**
```bash
cd backend
pip install -r requirements.txt
# .env at repo root needs DATABASE_URL_DIRECT and DATABASE_URL_POOLED — see .env.example
uvicorn backend.main:app --reload --port 8000
```
Interactive API docs: `http://localhost:8000/docs`

**3. Frontend:**
```bash
cd frontend
npm install
cp .env.local.example .env.local   # set BACKEND_API_URL=http://localhost:8000
npm run dev
```
Dashboard: `http://localhost:3000`

## Dataset

[PaySim — Synthetic Financial Datasets For Fraud Detection](https://www.kaggle.com/datasets/ealaxi/paysim1) (Kaggle). 6,362,620 transactions, hourly `step` column, `isFraud`/`isFlaggedFraud` labels, 5 transaction types. Not included in this repo (470MB) — download from the link above and place at `data/raw/paysim.csv`.

## License

MIT