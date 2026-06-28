"use client";

import { useState, FormEvent } from "react";
import type { AccountVelocity } from "@/lib/api";
import { LoadingSkeleton, ErrorPanel } from "@/components/StatusPanels";
import {
  formatCurrencyFull,
  formatCount,
  formatPercent,
} from "@/lib/format";
import { clsx } from "clsx";

type SearchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "success"; data: AccountVelocity };

const EXAMPLE_ACCOUNTS = ["C1231006815", "C1666544295", "M1979787155"];

/**
 * Client component (search is inherently interactive). Calls
 * app/api/velocity/[accountId]/route.ts rather than the FastAPI backend
 * directly — see that route's docstring and the architecture note in
 * next.config.mjs.
 *
 * Surfaces the backend's two distinct 404 messages verbatim (see
 * backend/routers/velocity.py) rather than rewriting them into a single
 * generic "not found" — that distinction was deliberately engineered on
 * the backend and is worth preserving all the way to the UI.
 */
export default function VelocityPage() {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<SearchState>({ status: "idle" });

  async function handleSearch(e: FormEvent) {
    e.preventDefault();
    const accountId = query.trim();
    if (!accountId) return;

    setState({ status: "loading" });
    try {
      const res = await fetch(`/api/velocity/${encodeURIComponent(accountId)}`);
      const body = await res.json();
      if (!res.ok) {
        setState({ status: "error", message: body.detail ?? "Account not found." });
        return;
      }
      setState({ status: "success", data: body as AccountVelocity });
    } catch {
      setState({
        status: "error",
        message: "Could not reach the backend. Try again.",
      });
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-mono text-lg font-medium text-text-primary">
          velocity
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Outbound transaction velocity for a single account — count, volume,
          counterparties, and active time span.
        </p>
      </div>

      <form onSubmit={handleSearch} className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="account id, e.g. C1231006815"
          aria-label="Account ID"
          className="flex-1 rounded-lg border border-hairline bg-raised px-3 py-2 font-mono text-sm text-text-primary placeholder:text-text-muted focus-visible:border-safe"
        />
        <button
          type="submit"
          disabled={state.status === "loading"}
          className="rounded-lg bg-safe px-4 py-2 font-mono text-sm font-medium text-void transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          look up
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-2xs text-text-muted">try:</span>
        {EXAMPLE_ACCOUNTS.map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => setQuery(id)}
            className="rounded border border-hairline px-2 py-0.5 font-mono text-2xs text-text-secondary hover:border-text-muted hover:text-text-primary"
          >
            {id}
          </button>
        ))}
      </div>

      {state.status === "loading" && <LoadingSkeleton rows={2} />}
      {state.status === "error" && <ErrorPanel message={state.message} />}
      {state.status === "success" && <VelocityResult data={state.data} />}
    </div>
  );
}

function VelocityResult({ data }: { data: AccountVelocity }) {
  const hasFraud = data.fraud_transactions > 0;

  return (
    <section
      className={clsx(
        "rounded-lg border bg-raised p-5",
        hasFraud ? "border-fraud-dim" : "border-hairline",
      )}
    >
      <div className="flex items-start justify-between">
        <div>
          <h2 className="font-mono text-base font-semibold text-text-primary">
            {data.account_id}
          </h2>
          <p className="mt-0.5 font-mono text-2xs uppercase tracking-wider text-text-muted">
            {data.account_type}
          </p>
        </div>
        {hasFraud && (
          <span className="rounded-full bg-fraud/15 px-2.5 py-1 font-mono text-2xs uppercase tracking-wide text-fraud">
            {formatCount(data.fraud_transactions)} fraud txn
            {data.fraud_transactions > 1 ? "s" : ""}
          </span>
        )}
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-4 md:grid-cols-3">
        <Stat label="total transactions" value={formatCount(data.total_transactions)} />
        <Stat
          label="distinct counterparties"
          value={formatCount(data.distinct_counterparties)}
        />
        <Stat label="total amount sent" value={formatCurrencyFull(data.total_amount_sent)} />
        <Stat label="avg amount sent" value={formatCurrencyFull(data.avg_amount_sent)} />
        <Stat label="max amount sent" value={formatCurrencyFull(data.max_amount_sent)} />
        <Stat
          label="active step span"
          value={
            data.active_step_span === 0
              ? "single transaction"
              : `${formatCount(data.active_step_span)} steps`
          }
        />
        <Stat label="first activity" value={`step ${data.first_step}`} />
        <Stat label="last activity" value={`step ${data.last_step}`} />
        <Stat
          label="avg transactions / step"
          value={
            data.active_step_span === 0
              ? "n/a"
              : parseFloat(data.avg_transactions_per_step).toFixed(4)
          }
        />
      </dl>

      {data.active_step_span === 0 && data.total_transactions === 1 && (
        <p className="mt-4 rounded bg-raised-2 px-3 py-2 font-mono text-2xs text-text-muted">
          Single-transaction account — no meaningful velocity to compute.
          See findings_report.md §6: this pattern (one large, isolated
          transaction) is this dataset&apos;s dominant high-value fraud
          signature, distinct from sustained-activity mule patterns.
        </p>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-2xs uppercase tracking-wider text-text-muted">
        {label}
      </dt>
      <dd className="mt-0.5 font-mono text-sm font-medium tabular-nums text-text-primary">
        {value}
      </dd>
    </div>
  );
}
