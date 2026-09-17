import { NextResponse } from "next/server";
import { apiBaseUrl, internalAuthHeaders } from "@/lib/api-client";
import { isApiError, requireSessionApi } from "@/lib/authz";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await requireSessionApi();
  if (isApiError(session)) return session;
  const { id } = await params;

  const upstream = await fetch(`${apiBaseUrl()}/api/jobs/${encodeURIComponent(id)}`, {
    headers: internalAuthHeaders(session.user.email),
    cache: "no-store",
  });
  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
}
