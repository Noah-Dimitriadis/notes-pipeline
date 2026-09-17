import { NextResponse } from "next/server";
import { apiBaseUrl, internalAuthHeaders } from "@/lib/api-client";
import { isApiError, requireSessionApi } from "@/lib/authz";

// Write-only proxy: the key value passes through this handler in one
// direction only (browser -> api service) and is never logged, echoed, or
// stored on this side (§14.5 M14: "never stores ciphertext itself").
export async function POST(req: Request) {
  const session = await requireSessionApi();
  if (isApiError(session)) return session;

  const payload = await req.json();
  const upstream = await fetch(`${apiBaseUrl()}/api/me/anthropic-key`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...internalAuthHeaders(session.user.email),
    },
    body: JSON.stringify({ api_key: payload.api_key }),
  });
  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
