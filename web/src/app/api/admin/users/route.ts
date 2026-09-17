import { NextResponse } from "next/server";
import { addUser, listUsers } from "@/lib/notes-db";
import { isApiError, requireAdminApi } from "@/lib/authz";

// Admin-only, and gated a second time here (not just by middleware's
// APP_MODE check) since these routes write directly to notes.db's shared
// `users` table (§14.3) — the same table Python's api service depends on.

export async function GET() {
  const session = await requireAdminApi();
  if (isApiError(session)) return session;
  return NextResponse.json(listUsers());
}

export async function POST(req: Request) {
  const session = await requireAdminApi();
  if (isApiError(session)) return session;
  const { email } = await req.json();
  if (!email || typeof email !== "string") {
    return NextResponse.json({ error: "email is required" }, { status: 400 });
  }
  addUser(email.trim().toLowerCase());
  return NextResponse.json({ ok: true });
}
