import { NextResponse } from "next/server";
import { api, ApiError } from "@/lib/api";

/**
 * Proxies GET /velocity/{accountId} to the FastAPI backend. Same
 * rationale as app/api/fraud-patterns/route.ts: this is called from a
 * client component (the account search input), so it goes through this
 * same-origin route rather than hitting FastAPI directly from the
 * browser.
 *
 * Correctly forwards the backend's two distinct 404 messages (account
 * doesn't exist vs. account exists but has no outbound transactions —
 * see backend/routers/velocity.py) rather than collapsing both into a
 * generic error, preserving the distinction the backend was specifically
 * designed to make.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ accountId: string }> },
) {
  const { accountId } = await params;

  try {
    const data = await api.velocity.getAccount(accountId);
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json(
        { detail: err.detail ?? err.message },
        { status: err.status },
      );
    }
    return NextResponse.json({ detail: "Unexpected error." }, { status: 500 });
  }
}
