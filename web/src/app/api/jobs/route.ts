import { NextResponse } from "next/server";
import { apiBaseUrl, internalAuthHeaders } from "@/lib/api-client";
import { isApiError, requireSessionApi } from "@/lib/authz";

export const dynamic = "force-dynamic";

// Persistent job history (newest first) — unlike /api/jobs/[id], this
// survives an `api` restart since it's always read from the `jobs` table,
// never the in-memory job dict.
export async function GET() {
  const session = await requireSessionApi();
  if (isApiError(session)) return session;

  const upstream = await fetch(`${apiBaseUrl()}/api/jobs`, {
    headers: internalAuthHeaders(session.user.email),
    cache: "no-store",
  });
  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
