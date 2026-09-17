import { NextResponse } from "next/server";
import { apiBaseUrl, internalAuthHeaders } from "@/lib/api-client";
import { isApiError, requireSessionApi } from "@/lib/authz";

export const dynamic = "force-dynamic";

export async function GET() {
  const session = await requireSessionApi();
  if (isApiError(session)) return session;

  const upstream = await fetch(`${apiBaseUrl()}/api/lectures`, {
    headers: internalAuthHeaders(session.user.email),
    cache: "no-store",
  });
  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}

// Streams the multipart upload straight through to the api service —
// `req.body` is forwarded as-is, never buffered into memory here, since
// lecture audio can be tens or hundreds of MB (PLAN.md §14.5 M14).
export async function POST(req: Request) {
  const session = await requireSessionApi();
  if (isApiError(session)) return session;

  const upstream = await fetch(`${apiBaseUrl()}/api/lectures`, {
    method: "POST",
    headers: {
      "content-type": req.headers.get("content-type") ?? "",
      ...internalAuthHeaders(session.user.email),
    },
    body: req.body,
    // @ts-expect-error - `duplex` isn't in the lib.dom.d.ts RequestInit type
    // yet, but Node's fetch (undici) requires it for a streaming body.
    duplex: "half",
  });
  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
