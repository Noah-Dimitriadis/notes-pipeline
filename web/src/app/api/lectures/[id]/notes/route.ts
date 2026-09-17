import { NextResponse } from "next/server";
import { apiBaseUrl, internalAuthHeaders } from "@/lib/api-client";
import { isApiError, requireSessionApi } from "@/lib/authz";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await requireSessionApi();
  if (isApiError(session)) return session;
  const { id } = await params;

  const upstream = await fetch(`${apiBaseUrl()}/api/lectures/${encodeURIComponent(id)}/notes`, {
    headers: internalAuthHeaders(session.user.email),
    cache: "no-store",
  });
  if (!upstream.ok) {
    const detail = await upstream.text();
    return NextResponse.json({ error: detail }, { status: upstream.status });
  }
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/markdown; charset=utf-8",
      "content-disposition": `attachment; filename="${id}.md"`,
    },
  });
}
