import { redirect } from "next/navigation";
import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { DEV_BYPASS_EMAIL, isAuthEnabled } from "./env";

type ActiveSession = { user: { id: string; email: string; disabled: boolean } };

// Local-testing escape hatch, mirroring notes_pipeline/web_config.py's
// AUTH_ENABLED exactly (same default-true, same DEV_BYPASS_EMAIL) — every
// requireSession*/requireAdmin* call below short-circuits to this fixed
// identity instead of touching Auth.js at all, so `web`/`admin` are both
// fully usable with no Google credentials configured. Never true anywhere
// but a local dev machine; nothing here enforces that for you.
const BYPASS_SESSION: ActiveSession = {
  user: { id: DEV_BYPASS_EMAIL, email: DEV_BYPASS_EMAIL, disabled: false },
};

/** For server components/pages: redirects to /login rather than rendering
 * anything for a missing or disabled session. */
export async function requireSession(): Promise<ActiveSession> {
  if (!isAuthEnabled()) return BYPASS_SESSION;
  const session = await auth();
  if (!session?.user?.email) redirect("/login");
  if (session.user.disabled) redirect("/login?error=disabled");
  return session as ActiveSession;
}

export async function requireAdmin(): Promise<ActiveSession> {
  const session = await requireSession();
  if (!isAuthEnabled()) return session;
  if (session.user.email !== process.env.ADMIN_EMAIL) redirect("/login?error=forbidden");
  return session;
}

/** For Route Handlers (JSON APIs): a 401/403 response instead of a
 * redirect, since these are called by fetch(), not the browser directly. */
export async function requireSessionApi(): Promise<ActiveSession | NextResponse> {
  if (!isAuthEnabled()) return BYPASS_SESSION;
  const session = await auth();
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }
  if (session.user.disabled) {
    return NextResponse.json({ error: "Account disabled." }, { status: 403 });
  }
  return session as ActiveSession;
}

export function isApiError(value: ActiveSession | NextResponse): value is NextResponse {
  return value instanceof NextResponse;
}

export async function requireAdminApi(): Promise<ActiveSession | NextResponse> {
  const session = await requireSessionApi();
  if (isApiError(session)) return session;
  if (!isAuthEnabled()) return session;
  if (session.user.email !== process.env.ADMIN_EMAIL) {
    return NextResponse.json({ error: "Admin only." }, { status: 403 });
  }
  return session;
}
