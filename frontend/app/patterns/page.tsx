"use client";

import { useEffect, useState, useCallback } from "react";
import type { SegmentFraudRate, TransactionType, AmountBucket } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { RiskHeatmap } from "@/components/RiskHeatmap";
import { LoadingSkeleton, ErrorPanel, EmptyPanel } from "@/components/StatusPanels";
import {
  formatPercent,
  formatCurrencyFull,
  formatCount,
} from "@/lib/format";
import { clsx } from "clsx";

const TYPE_OPTIONS: TransactionType[] = [
  "PAYMENT",
  "TRANSFER",
  "CASH_OUT",
  "CASH_IN",
  "DEBIT",
];
const BUCKET_OPTIONS: AmountBucket[] = ["0-1K", "1K-10K", "10K-100K", "100K-1M", "1M+"];

/**
 * Client component: filters are interactive (clicking a chip re-fetches),
 * and the heatmap cell click drives a detail panel — both need client-
 * side state, which is why this page (unlike the overview page) goes
 * through app/api/fraud-patterns/route.ts rather than calling the typed
 * client directly. See lib/api.ts and next.config.mjs for the full
 * rationale on this server/client split.
 */
export default function PatternsPage() {
  const [segments, setSegments] = useState<SegmentFraudRate[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [typeFilter, setTypeFilter] = useState<TransactionType | null>(null);
  const [bucketFilter, setBucketFilter] = useState<AmountBucket | null>(null);
  const [selected, setSelected] = useState<SegmentFraudRate | null>(null);

  const fetchSegments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (typeFilter) params.set("type_name", typeFilter);
      if (bucketFilter) params.set("amount_bucket", bucketFilter);

      const res = await fetch(`/api/fraud-patterns?${params.toString()}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(body.detail ?? "Request failed", res.status);
      }
      const data: SegmentFraudRate[] = await res.json();
      setSegments(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load fraud patterns.",
      );
    } finally {
      setLoading(false);
    }
  }, [typeFilter, bucketFilter]);

  useEffect(() => {
    fetchSegments();
  }, [fetchSegments]);

  // The heatmap always wants the FULL unfiltered segment set (it's a
  // grid — every cell needs to render, filters dim/select rather than
  // remove cells), so we fetch unfiltered data for the grid and use the
  // active filters only to drive which cells are highlighted/selected,
  // not what's fetched. Re-fetching here is intentionally a no-op when
  // typeFilter/bucketFilter change for THIS reason — but we still call
  // the API with filters applied for the detail list below the grid,
  // since that list benefits from genuinely narrowing down.
  const filteredForList = (segments ?? []).filter((s) => {
    if (typeFilter && s.type_name !== typeFilter) return false;
    if (bucketFilter && s.amount_bucket !== bucketFilter) return false;
    return true;
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-mono text-lg font-medium text-text-primary">
          fraud patterns
        </h1>
        <p className="mt-1 text-sm text-text-secondary">
          Fraud rate and volume by transaction type and amount range. Click
          any cell for detail.
        </p>
      </div>

      <FilterBar
        typeFilter={typeFilter}
        bucketFilter={bucketFilter}
        onTypeChange={setTypeFilter}
        onBucketChange={setBucketFilter}
      />

      <section className="rounded-lg border border-hairline bg-raised p-4">
        {loading && <LoadingSkeleton rows={5} />}
        {error && <ErrorPanel message={error} />}
        {!loading && !error && segments && segments.length === 0 && (
          <EmptyPanel message="No segments match the current filters." />
        )}
        {!loading && !error && segments && segments.length > 0 && (
          <RiskHeatmap
            segments={segments}
            onSelect={setSelected}
            selected={
              selected
                ? { type_name: selected.type_name, amount_bucket: selected.amount_bucket }
                : null
            }
          />
        )}
      </section>

      {selected && <SegmentDetail segment={selected} onClose={() => setSelected(null)} />}

      {!loading && !error && filteredForList.length > 0 && (
        <SegmentTable segments={filteredForList} />
      )}
    </div>
  );
}

function FilterBar({
  typeFilter,
  bucketFilter,
  onTypeChange,
  onBucketChange,
}: {
  typeFilter: TransactionType | null;
  bucketFilter: AmountBucket | null;
  onTypeChange: (v: TransactionType | null) => void;
  onBucketChange: (v: AmountBucket | null) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="font-mono text-2xs uppercase tracking-wider text-text-muted">
        type:
      </span>
      <Chip active={typeFilter === null} onClick={() => onTypeChange(null)}>
        all
      </Chip>
      {TYPE_OPTIONS.map((t) => (
        <Chip key={t} active={typeFilter === t} onClick={() => onTypeChange(t)}>
          {t}
        </Chip>
      ))}

      <span className="ml-3 font-mono text-2xs uppercase tracking-wider text-text-muted">
        amount:
      </span>
      <Chip active={bucketFilter === null} onClick={() => onBucketChange(null)}>
        all
      </Chip>
      {BUCKET_OPTIONS.map((b) => (
        <Chip key={b} active={bucketFilter === b} onClick={() => onBucketChange(b)}>
          {b}
        </Chip>
      ))}
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded-full border px-2.5 py-1 font-mono text-2xs uppercase tracking-wide transition-colors",
        active
          ? "border-safe bg-safe/10 text-safe"
          : "border-hairline text-text-secondary hover:border-text-muted hover:text-text-primary",
      )}
    >
      {children}
    </button>
  );
}

function SegmentDetail({
  segment,
  onClose,
}: {
  segment: SegmentFraudRate;
  onClose: () => void;
}) {
  return (
    <section className="rounded-lg border border-fraud-dim bg-raised p-4">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="font-mono text-sm font-medium text-text-primary">
            {segment.type_name} · {segment.amount_bucket}
          </h2>
          <p className="mt-0.5 text-2xs text-text-muted">
            {formatCount(segment.total_transactions)} transactions in this segment
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close detail"
          className="font-mono text-2xs text-text-muted hover:text-text-primary"
        >
          ✕
        </button>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
        <DetailStat label="fraud rate" value={formatPercent(segment.fraud_rate_pct)} tone="fraud" />
        <DetailStat
          label="fraud transactions"
          value={formatCount(segment.fraud_transactions)}
          tone="fraud"
        />
        <DetailStat
          label="fraud amount"
          value={formatCurrencyFull(segment.fraud_amount)}
          tone="fraud"
        />
        <DetailStat label="avg amount" value={formatCurrencyFull(segment.avg_amount)} />
      </dl>
    </section>
  );
}

function DetailStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "fraud";
}) {
  return (
    <div>
      <dt className="font-mono text-2xs uppercase tracking-wider text-text-muted">
        {label}
      </dt>
      <dd
        className={clsx(
          "mt-0.5 font-mono text-base font-semibold tabular-nums",
          tone === "fraud" ? "text-fraud" : "text-text-primary",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function SegmentTable({ segments }: { segments: SegmentFraudRate[] }) {
  const sorted = [...segments].sort(
    (a, b) => parseFloat(b.fraud_rate_pct) - parseFloat(a.fraud_rate_pct),
  );

  return (
    <section className="overflow-x-auto rounded-lg border border-hairline bg-raised">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-hairline font-mono text-2xs uppercase tracking-wider text-text-muted">
            <th className="px-4 py-2">type</th>
            <th className="px-4 py-2">bucket</th>
            <th className="px-4 py-2 text-right">transactions</th>
            <th className="px-4 py-2 text-right">fraud rate</th>
            <th className="px-4 py-2 text-right">fraud amount</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr
              key={`${s.type_name}-${s.amount_bucket}`}
              className="border-b border-hairline/50 font-mono text-xs last:border-0"
            >
              <td className="px-4 py-2 text-text-secondary">{s.type_name}</td>
              <td className="px-4 py-2 text-text-secondary">{s.amount_bucket}</td>
              <td className="px-4 py-2 text-right tabular-nums text-text-primary">
                {formatCount(s.total_transactions)}
              </td>
              <td
                className={clsx(
                  "px-4 py-2 text-right tabular-nums",
                  parseFloat(s.fraud_rate_pct) > 0 ? "text-fraud" : "text-text-muted",
                )}
              >
                {formatPercent(s.fraud_rate_pct)}
              </td>
              <td className="px-4 py-2 text-right tabular-nums text-text-secondary">
                {formatCurrencyFull(s.fraud_amount)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
