/**
 * api.ts — Typed client for the FinGuard Analytics FastAPI backend.
 *
 * Every type below mirrors backend/schemas.py's Pydantic models field-for-
 * field, not approximated — if the backend schema changes, these types
 * must change with it, which is the entire point of keeping them this
 * literal rather than loosely typing the response as `any`.
 *
 * NUMERIC FIELDS: the backend serializes Python Decimal as a JSON string
 * by default under FastAPI's standard response encoding (preserves exact
 * precision, avoids float rounding in transit) — these arrive as `string`
 * here, not `number`, and must be parsed explicitly at the point of use
 * (see lib/format.ts). Typing them as `string` rather than `number` is
 * deliberate, not an oversight: it forces every consumer to make an
 * explicit, visible choice about precision instead of silently floating
 * through a financial figure.
 */

export type TransactionType =
  | "PAYMENT"
  | "TRANSFER"
  | "CASH_OUT"
  | "CASH_IN"
  | "DEBIT";

export type AmountBucket = "0-1K" | "1K-10K" | "10K-100K" | "100K-1M" | "1M+";

export type AccountType = "CUSTOMER" | "MERCHANT";

export interface HourlyFraudSummary {
  step: number;
  total_transactions: number;
  fraud_transactions: number;
  fraud_rate_pct: string;
  total_amount: string;
  fraud_amount: string | null;
  avg_amount: string;
  flagged_fraud_count: number;
  false_positive_flags: number;
  missed_fraud_count: number;
}

export interface OverviewKPIs {
  total_transactions: number;
  total_fraud_transactions: number;
  overall_fraud_rate_pct: string;
  total_amount: string;
  total_fraud_amount: string;
  total_flagged_fraud_count: number;
  total_false_positive_flags: number;
  total_missed_fraud_count: number;
  step_range_min: number;
  step_range_max: number;
}

export interface SegmentFraudRate {
  type_name: TransactionType;
  amount_bucket: AmountBucket;
  amount_bucket_sort_order: number;
  total_transactions: number;
  fraud_transactions: number;
  fraud_rate_pct: string;
  total_amount: string;
  fraud_amount: string | null;
  avg_amount: string;
}

export interface AccountVelocity {
  account_key: number;
  account_id: string;
  account_type: AccountType;
  total_transactions: number;
  fraud_transactions: number;
  distinct_counterparties: number;
  total_amount_sent: string;
  avg_amount_sent: string;
  max_amount_sent: string;
  first_step: number;
  last_step: number;
  active_step_span: number;
  avg_transactions_per_step: string;
}

export interface FraudPatternFilterParams {
  type_name?: TransactionType;
  amount_bucket?: AmountBucket;
  min_fraud_rate_pct?: number;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Base URL resolution: server-side code (route handlers, server
 * components) reads BACKEND_API_URL directly from the environment —
 * this function is only ever called from server-side contexts in this
 * app (see architecture note in next.config.mjs), so process.env access
 * here is always safe and never bundled into client JS.
 */
function getBackendBaseUrl(): string {
  const url = process.env.BACKEND_API_URL;
  if (!url) {
    throw new Error(
      "BACKEND_API_URL is not set. Add it to .env.local for development " +
        "or your deploy platform's environment variables for production.",
    );
  }
  return url.replace(/\/$/, ""); // strip any trailing slash for clean concatenation
}

/**
 * Shared fetch wrapper: builds the full URL, attaches a sane timeout,
 * and converts a non-2xx response into a typed ApiError carrying the
 * backend's actual `detail` message (FastAPI's standard error shape)
 * rather than a generic "fetch failed" with no context — this is what
 * lets velocity.py's two distinct 404 messages actually reach the UI.
 */
async function apiFetch<T>(
  path: string,
  params?: Record<string, string | number | undefined | null>,
): Promise<T> {
  const base = getBackendBaseUrl();
  const url = new URL(`${base}${path}`);

  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10_000);

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      signal: controller.signal,
      // Server-side requests to our own backend should not be cached by
      // Next.js's data cache by default — this is live operational data
      // (fraud KPIs), not static content. Individual call sites can opt
      // into caching deliberately if a specific endpoint's staleness
      // tolerance justifies it.
      cache: "no-store",
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new ApiError("Request to backend timed out after 10s.", 504);
    }
    throw new ApiError(
      `Could not reach backend at ${base}. Is it running?`,
      503,
    );
  } finally {
    clearTimeout(timeoutId);
  }

  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = await response.json();
      detail = typeof body?.detail === "string" ? body.detail : undefined;
    } catch {
      // Response body wasn't JSON (or was empty) — fall through with no detail.
    }
    throw new ApiError(
      detail ?? `Request failed with status ${response.status}`,
      response.status,
      detail,
    );
  }

  return response.json() as Promise<T>;
}

export const api = {
  overview: {
    getKpis: () => apiFetch<OverviewKPIs>("/overview/kpis"),
    getTimeseries: (params?: { step_min?: number; step_max?: number }) =>
      apiFetch<HourlyFraudSummary[]>("/overview/timeseries", params),
  },
  fraudPatterns: {
    list: (params?: FraudPatternFilterParams) =>
      apiFetch<SegmentFraudRate[]>("/fraud-patterns/", params ? { ...params } : undefined),
    topRisk: (params?: { limit?: number; min_transactions?: number }) =>
      apiFetch<SegmentFraudRate[]>("/fraud-patterns/top-risk", params),
  },
  velocity: {
    getAccount: (accountId: string) =>
      apiFetch<AccountVelocity>(
        `/velocity/${encodeURIComponent(accountId)}`,
      ),
  },
};
