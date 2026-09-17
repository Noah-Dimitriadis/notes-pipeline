import { NextResponse } from "next/server";
import { setUserDisabled } from "@/lib/notes-db";
import { isApiError, requireAdminApi } from "@/lib/authz";

// Toggle only — "Remove" is soft-disable, never a hard delete (§14.5 M15).
export async function PATCH(req: Request, { params }: { params: Promise<{ email: string }> }) {
  const session = await requireAdminApi();
  if (isApiError(session)) return session;
  const { email } = await params;
  const { disabled } = await req.json();
  setUserDisabled(decodeURIComponent(email), !!disabled);
  return NextResponse.json({ ok: true });
}
