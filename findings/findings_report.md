# FinGuard Analytics — Findings Report

**Dataset:** PaySim synthetic mobile-money transactions ([Kaggle](https://www.kaggle.com/datasets/ealaxi/paysim1)), full source size 6,362,620 transactions.
**Analysis sample:** 858,573 transactions (stratified — see Methodology), live in Supabase Postgres.
**Generated from:** live queries against the production schema, not derived/estimated figures. Query source: `findings_queries.sql`.

---

## 1. Methodology: Why This Is a Sample, Not the Full Dataset

Free-tier managed Postgres (Supabase, and equivalently Neon) enforces a storage quota — confirmed empirically during this project's build at ~500 MB before the database is flipped to read-only. Loading the full 6,362,620-row fact table plus dimension tables and indexing exceeded this quota during initial load testing (the database was observed entering read-only mode at ~362 MB with the dimension load alone still in progress at that point in testing).

Rather than switch to a smaller, less representative dataset, the load was redesigned around **stratified sampling**:

- **100% of fraud transactions retained** — every one of the 8,213 fraud rows in the full dataset is present in this sample. Verified: this sample's `fraud_rows` count (8,213) is checked against the full dataset's known total fraud count and matches exactly to the row.
- **Non-fraud transactions sampled at a fixed, deterministic rate** (~13.4%), targeting a row budget sized from an empirical per-row storage measurement taken during a smaller test load (100,000 rows ≈ 48.4 MB all-in, including proportional dimension/index overhead), back-calculated to fit comfortably under the storage cap with headroom for materialized views.
- **Sampling is deterministic and reproducible**, not a random draw re-rolled on every load — implemented as a seeded hash of stable row-identifying fields (origin account, destination account, time step, amount), so the same source rows are selected every time the loader runs against this CSV.

**Resulting sample composition** (Query 1):

| Metric | Value |
|---|---|
| Total transactions in sample | 858,573 |
| Fraud transactions in sample | 8,213 |
| Sample fraud rate | 0.9566% |
| Full-dataset fraud rate (known) | 0.1291% |
| **Enrichment factor** | **7.41x** |

This is a deliberate trade-off, not a limitation glossed over: the sample is **not representative of overall transaction volume** (it under-represents legitimate transactions by design), but it is **fully representative of fraud patterns**, since every fraud case in the source data is present. Any finding below about *where fraud occurs* is drawn from the complete fraud population. Any finding about *overall transaction volume* should be read as derived from a ~13.5% sample of legitimate activity.

**Database footprint at this sample size** (Query 7): **491 MB total**, of which the project's own tables — `dim_account` (179 MB), `fact_transactions` (172 MB), `mv_account_velocity` (129 MB), plus two small materialized views — account for essentially all of it. The remainder is Supabase's own default schema scaffolding (auth/storage tables present on every new project regardless of usage), not part of this project's data.

---

## 2. Finding: Fraud Is Structurally Confined to Two Transaction Types

PaySim's fraud is not spread across all transaction types — it is exclusively present in `TRANSFER` and `CASH_OUT` (Query 2):

| Type | Total Transactions | Fraud Transactions | Fraud Rate |
|---|---|---|---|
| TRANSFER | 75,380 | 4,097 | 5.4351% |
| CASH_OUT | 302,979 | 4,116 | 1.3585% |
| CASH_IN | 187,119 | 0 | 0.0000% |
| DEBIT | 5,356 | 0 | 0.0000% |
| PAYMENT | 287,739 | 0 | 0.0000% |

`PAYMENT`, `CASH_IN`, and `DEBIT` carry **zero** fraud across all 480,214 sampled transactions of those types combined. This is a property of the source dataset's construction, not an artifact of sampling — confirmed by the fact that 100% of fraud rows are retained in this sample, so a zero count here means zero in the full dataset too.

**Practical implication:** any fraud-detection feature engineering or rule-based filtering on this dataset should treat `PAYMENT`/`CASH_IN`/`DEBIT` as out-of-scope for fraud modeling — including them dilutes a model's effective signal without contributing any positive cases.

---

## 3. Finding: Fraud Risk Is Concentrated at the High End of CASH_OUT

Breaking `TRANSFER` and `CASH_OUT` down by amount bucket (Query 3, full 23-segment breakdown) surfaces the single sharpest signal in the dataset:

| Type | Amount Bucket | Transactions | Fraud Count | Fraud Rate | Share of Total Fraud $ |
|---|---|---|---|---|---|
| TRANSFER | 1M+ | 18,951 | 1,361 | 7.18% | 43.7% |
| **CASH_OUT** | **1M+** | **1,382** | **1,345** | **97.32%** | **43.0%** |
| TRANSFER | 100K-1M | 47,280 | 1,894 | 4.01% | 6.3% |
| CASH_OUT | 100K-1M | 197,294 | 1,906 | 0.97% | 6.3% |
| TRANSFER | 10K-100K | 8,211 | 711 | 8.66% | 0.3% |
| CASH_OUT | 10K-100K | 94,363 | 718 | 0.76% | 0.3% |
| TRANSFER | 1K-10K | 839 | 110 | 13.11% | <0.1% |
| CASH_OUT | 1K-10K | 8,939 | 110 | 1.23% | <0.1% |
| TRANSFER | 0-1K | 99 | 21 | 21.21% | <0.1% |
| CASH_OUT | 0-1K | 1,001 | 37 | 3.70% | <0.1% |

(Total fraud dollar value across both types: $12.06B. "Share of Total Fraud $" is each segment's fraud amount as a percentage of that $12.06B total — not as a percentage of the segment's own transaction volume, which is the separate "Fraud Rate" column.)

**Two segments together account for 86.7% of all fraud dollar value, despite being structurally different in shape.** `CASH_OUT` 1M+ has a small population (1,382 transactions) with an extreme fraud *rate* (97.32%) — almost every transaction in that bucket is fraud. `TRANSFER` 1M+ has a much larger population (18,951 transactions) with a comparatively modest fraud rate (7.18%) but, because the segment is so much larger, contributes essentially the same total fraud dollar value (43.7% vs 43.0%). These are two different detection problems: `CASH_OUT` 1M+ is "almost everything here is bad, flag the whole bucket"; `TRANSFER` 1M+ is "most of this is legitimate, the fraud is hiding inside a much larger population and needs finer-grained signal to isolate."

`TRANSFER`'s fraud *rate* (not dollar share) is also worth noting separately: it is actually highest at the smallest amounts (21.21% at 0-1K) and declines as amount increases up to the 100K-1M bucket, before rising again at 1M+. This rate-based pattern is real but contributes negligible dollar value (the 0-1K and 1K-10K buckets are each under 0.1% of total fraud dollars) precisely because the dollar amounts involved are small — a useful reminder that "highest fraud rate" and "highest fraud dollar impact" are different rankings and can point a fraud team toward different buckets depending on which cost (review effort vs. dollar loss) is being optimized against.

**Practical implication:** a single rule — "flag any CASH_OUT transaction exceeding 1,000,000" — would catch 1,345 of the dataset's 8,213 fraud cases (16.4% of all fraud cases, 43.0% of all fraud dollars) while only requiring review of 1,382 transactions total, a 97% hit rate on review volume. This is the kind of simple, explainable rule a real fraud team would deploy as a first-line filter ahead of (or alongside) any ML-based system — though Section 4 shows PaySim's own attempt at exactly this kind of rule performs far worse than this one does, which is itself an instructive contrast.

---

## 4. Finding: PaySim's Own Detection Flag Catches Almost No Fraud

PaySim ships with its own `isFlaggedFraud` field — ostensibly the dataset's "ground truth" fraud-detection rule. Comparing it against actual fraud outcomes (Query 4) reveals it is far weaker than its presence in the schema might suggest:

| Metric | Value |
|---|---|
| Total flagged by PaySim's rule | 16 |
| False positives (flagged, not actually fraud) | 0 |
| Missed fraud (actual fraud, not flagged) | 8,197 |
| Total actual fraud | 8,213 |
| **Fraud missed by the rule** | **99.81%** |

PaySim's built-in flag has **perfect precision** (0 false positives — everything it flags is genuinely fraud) but **catastrophic recall** (it catches only 16 of 8,213 fraud cases, missing 99.81%). This asymmetry is worth stating plainly: a detection rule with 0% false-positive rate sounds appealing in isolation, but a rule that misses 99.8% of the fraud it exists to catch has effectively no operational value as a standalone control.

**Practical implication:** this is the empirical justification for why a real fraud-monitoring system cannot rely on simple threshold rules alone (PaySim's flag is reportedly based on a single large-transfer-amount threshold) and needs either a richer rule set — Section 3's segment-level findings are a starting point — or a learned model with broader feature coverage. This dataset's own "ground truth" detector is a useful illustration of what *not* to ship as a complete solution.

---

## 5. Finding: Fraud Clusters Sharply in Specific Hours

Looking at fraud rate by `step` (Query 6), several hours show a **100% fraud rate** — every single transaction recorded in that hour, in this sample, was fraudulent (steps 28, 29, 30, 31, 32, 50, 51, 52, 53, 54, among others at the top of the ranking).

This is a real pattern in the underlying data, not a sampling artifact — these are hours with low overall transaction *volume* (4–14 transactions sampled per hour in the table above) where fraud happens to dominate the (small) set of transactions that occurred. This is worth stating carefully in any presentation of this finding: a 100% fraud rate on a 4-transaction hour is a much weaker signal than a 97% fraud rate on a 1,382-transaction segment (Section 3) — the segment-level finding is statistically sturdier and should be the headline; the hourly spikes are a secondary, lower-confidence observation appropriate for a time-series chart but not for a standalone claim.

---

## 6. Finding: High-Value Fraud Often Looks Like a Single, Large, One-Shot Transaction

The ten highest-fraud-volume accounts by total amount sent (Query 5) show a strikingly consistent pattern: **every one of the top 10 accounts has exactly 1 transaction, 1 distinct counterparty, an active step span of 0, and an amount of exactly $10,000,000.00.**

This describes a "smash and grab" fraud signature rather than a sustained mule-account pattern: a single customer account, observed only once, moving a large fixed amount to one counterparty, with no other activity before or after in the sampled window. This is a structurally different fraud archetype from the "many small transactions building velocity" pattern that `mv_account_velocity`'s `avg_transactions_per_step` metric was originally designed to surface — for *this* dataset, velocity-based detection would need to specifically handle the single-large-transaction case as its own category, since these accounts have no "velocity" in the traditional repeated-activity sense at all.

**Practical implication:** account-velocity-based fraud detection (count of transactions per unit time) is the wrong tool for catching this dataset's highest-value fraud cases — those are one-and-done events. A complementary "is this single transaction unusually large relative to the account's (often nonexistent) history" check is needed alongside any velocity-based rule.

---

## 7. Summary of Actionable Signals

1. Restrict fraud-detection scope to `TRANSFER` and `CASH_OUT` — the other three types contribute zero fraud cases in this dataset.
2. A single high-recall, low-review-cost rule (`CASH_OUT` > 1M) catches 16.4% of all fraud while flagging only 1,382 of 858,573 transactions for review.
3. PaySim's bundled `isFlaggedFraud` rule is not a usable detection baseline on its own (99.81% miss rate) — useful as a cautionary example, not as ground truth to build on.
4. The dataset's highest-value fraud cases are single, isolated, large transactions, not sustained velocity patterns — detection logic needs both a velocity-based path and a large-single-transaction path to cover both archetypes seen here.
5. Hourly fraud-rate spikes exist but are driven by low transaction volume in those hours — treat as a secondary/visual finding, not a primary statistical claim.

---

## Appendix: Query Reproducibility

All figures above were generated by `findings_queries.sql`, run directly against the live `finguard-analytics` Supabase instance via:

```
psql $DATABASE_URL_DIRECT -f findings_queries.sql
```

No figure in this report is estimated, extrapolated, or carried over from earlier development/testing output — each was generated specifically for this report from the final loaded dataset state.