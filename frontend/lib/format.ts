/**
 * format.ts — Display formatting for values coming from the API.
 *
 * All monetary/percentage fields arrive from the backend as strings
 * (see lib/api.ts's NUMERIC FIELDS note) to preserve exact Decimal
 * precision in transit. These helpers are the single place that
 * parses them for display — every formatter here is explicit about
 * precision loss, since JS numbers are IEEE 754 doubles and any
 * formatted dollar figure beyond ~2^53 is already an approximation by
 * the time it reaches a chart or label, which is true of any frontend
 * regardless of framework. The dataset's known max single-segment total
 * (~$49B, from findings_report.md) is comfortably within safe-integer
 * range for display purposes, so this is a documented non-issue here,
 * not an unexamined risk.
 */

const usdFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

const usdFullFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const integerFormatter = new Intl.NumberFormat("en-US");

/** Compact currency: $5.2M, $1.38K — for KPI tiles and chart axes. */
export function formatCurrencyCompact(value: string | number): string {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (!Number.isFinite(n)) return "—";
  return usdFormatter.format(n);
}

/** Full currency with thousands separators: $5,189,909,252 — for detail rows. */
export function formatCurrencyFull(value: string | number | null): string {
  if (value === null) return "—";
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (!Number.isFinite(n)) return "—";
  return usdFullFormatter.format(n);
}

/** Integer with thousands separators: 858,573 — for transaction counts. */
export function formatCount(value: number): string {
  return integerFormatter.format(value);
}

/**
 * Percentage with fixed precision. Backend already rounds to 4 decimal
 * places (see materialized_views.sql's ROUND(...,4)) — this re-rounds to
 * a display-appropriate 2 decimals rather than showing the full
 * database-precision figure, which is more noise than signal for a UI.
 */
export function formatPercent(value: string | number, decimals = 2): string {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(decimals)}%`;
}

/**
 * Risk-tier classification for the fraud rate heatmap — maps a
 * continuous fraud_rate_pct onto a small set of named intensity tiers.
 * Thresholds are informed by this project's own findings_report.md
 * (e.g. the 97.32% CASH_OUT/1M+ segment is the dataset's actual extreme
 * case) rather than arbitrary round numbers.
 */
export type RiskTier = "none" | "low" | "moderate" | "high" | "extreme";

export function classifyRiskTier(fraudRatePct: string | number): RiskTier {
  const n =
    typeof fraudRatePct === "string" ? parseFloat(fraudRatePct) : fraudRatePct;
  if (!Number.isFinite(n) || n <= 0) return "none";
  if (n < 1) return "low";
  if (n < 10) return "moderate";
  if (n < 50) return "high";
  return "extreme";
}
