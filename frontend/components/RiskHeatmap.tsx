"use client";

import { clsx } from "clsx";
import type { SegmentFraudRate, AmountBucket } from "@/lib/api";
import { classifyRiskTier, formatPercent, formatCurrencyCompact } from "@/lib/format";

const BUCKET_ORDER: AmountBucket[] = ["0-1K", "1K-10K", "10K-100K", "100K-1M", "1M+"];
const TYPE_ORDER = ["PAYMENT", "CASH_IN", "DEBIT", "TRANSFER", "CASH_OUT"] as const;

const TIER_STYLES: Record<string, string> = {
  none: "bg-raised-2 text-text-muted",
  low: "bg-safe-dim/40 text-safe",
  moderate: "bg-amber/20 text-amber",
  high: "bg-fraud-dim text-fraud",
  extreme: "bg-fraud text-void",
};

interface RiskHeatmapProps {
  segments: SegmentFraudRate[];
  onSelect?: (segment: SegmentFraudRate) => void;
  selected?: { type_name: string; amount_bucket: string } | null;
}

/**
 * The dashboard's signature visual: every (type, amount_bucket) segment
 * rendered as a grid cell, colored by fraud_rate_pct tier. This is
 * deliberately NOT a generic bar chart — a grid lets every segment be
 * visible simultaneously at a glance, which is the actual analyst task
 * this page serves ("where is risk concentrated, across everything, right
 * now") rather than a single drill-down view. Cells with zero data for a
 * given (type, bucket) combination render as empty/disabled rather than
 * being omitted, so the grid's shape stays a stable, scannable rectangle.
 */
export function RiskHeatmap({ segments, onSelect, selected }: RiskHeatmapProps) {
  const lookup = new Map(
    segments.map((s) => [`${s.type_name}__${s.amount_bucket}`, s]),
  );

  return (
    <div className="overflow-x-auto">
      <div
        className="grid min-w-[640px] gap-1"
        style={{ gridTemplateColumns: `100px repeat(${BUCKET_ORDER.length}, 1fr)` }}
        role="grid"
        aria-label="Fraud rate by transaction type and amount range"
      >
        <div />
        {BUCKET_ORDER.map((bucket) => (
          <div
            key={bucket}
            className="px-1 pb-1 text-center font-mono text-2xs uppercase tracking-wider text-text-muted"
          >
            {bucket}
          </div>
        ))}

        {TYPE_ORDER.map((type) => (
          <RowOf
            key={type}
            type={type}
            lookup={lookup}
            onSelect={onSelect}
            selected={selected}
          />
        ))}
      </div>

      <div className="mt-3 flex items-center gap-3 font-mono text-2xs text-text-muted">
        <span>fraud rate:</span>
        <LegendSwatch tier="none" label="0%" />
        <LegendSwatch tier="low" label="<1%" />
        <LegendSwatch tier="moderate" label="1–10%" />
        <LegendSwatch tier="high" label="10–50%" />
        <LegendSwatch tier="extreme" label="50%+" />
      </div>
    </div>
  );
}

function RowOf({
  type,
  lookup,
  onSelect,
  selected,
}: {
  type: string;
  lookup: Map<string, SegmentFraudRate>;
  onSelect?: (segment: SegmentFraudRate) => void;
  selected?: { type_name: string; amount_bucket: string } | null;
}) {
  return (
    <>
      <div className="flex items-center font-mono text-2xs uppercase tracking-wider text-text-secondary">
        {type}
      </div>
      {BUCKET_ORDER.map((bucket) => {
        const seg = lookup.get(`${type}__${bucket}`);
        return (
          <HeatCell
            key={bucket}
            segment={seg}
            onSelect={onSelect}
            isSelected={
              !!selected &&
              selected.type_name === type &&
              selected.amount_bucket === bucket
            }
          />
        );
      })}
    </>
  );
}

function HeatCell({
  segment,
  onSelect,
  isSelected,
}: {
  segment?: SegmentFraudRate;
  onSelect?: (segment: SegmentFraudRate) => void;
  isSelected: boolean;
}) {
  if (!segment) {
    return (
      <div
        className="flex h-16 items-center justify-center rounded bg-raised-2/40 font-mono text-2xs text-text-muted"
        aria-hidden="true"
      >
        n/a
      </div>
    );
  }

  const tier = classifyRiskTier(segment.fraud_rate_pct);

  return (
    <button
      type="button"
      onClick={() => onSelect?.(segment)}
      className={clsx(
        "flex h-16 flex-col items-center justify-center gap-0.5 rounded transition-all",
        TIER_STYLES[tier],
        isSelected
          ? "ring-2 ring-text-primary ring-offset-2 ring-offset-void"
          : "hover:brightness-110",
        tier === "extreme" && "animate-pulse-fraud",
      )}
      aria-label={`${segment.type_name}, ${segment.amount_bucket}: ${formatPercent(
        segment.fraud_rate_pct,
      )} fraud rate, ${formatCurrencyCompact(segment.fraud_amount ?? 0)} in fraud`}
      title={`${segment.total_transactions.toLocaleString()} transactions`}
    >
      <span className="font-mono text-sm font-semibold tabular-nums">
        {formatPercent(segment.fraud_rate_pct, 1)}
      </span>
      {segment.fraud_amount && (
        <span className="font-mono text-2xs tabular-nums opacity-80">
          {formatCurrencyCompact(segment.fraud_amount)}
        </span>
      )}
    </button>
  );
}

function LegendSwatch({ tier, label }: { tier: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className={clsx("h-3 w-3 rounded-sm", TIER_STYLES[tier])} />
      {label}
    </span>
  );
}
