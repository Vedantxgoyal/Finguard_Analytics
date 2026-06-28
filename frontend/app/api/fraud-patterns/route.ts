import { NextRequest, NextResponse } from "next/server";
import { api, ApiError, type TransactionType, type AmountBucket } from "@/lib/api";

/**
 * Proxies GET /fraud-patterns/ to the FastAPI backend. Exists because
 * this route is called from a CLIENT component (the interactive heatmap
 * filter UI) — the browser hits this same-origin Next.js route instead
 * of the FastAPI backend directly, so the backend's URL and CORS
 * configuration never need to be exposed to client-side JS at all (see
 * the architecture note in next.config.mjs).
 */
export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;

  const type_name = searchParams.get("type_name") as TransactionType | null;
  const amount_bucket = searchParams.get("amount_bucket") as AmountBucket | null;
  const min_fraud_rate_pct = searchParams.get("min_fraud_rate_pct");

  try {
    const data = await api.fraudPatterns.list({
      type_name: type_name ?? undefined,
      amount_bucket: amount_bucket ?? undefined,
      min_fraud_rate_pct: min_fraud_rate_pct ? Number(min_fraud_rate_pct) : undefined,
    });
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json({ detail: err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "Unexpected error." }, { status: 500 });
  }
}
