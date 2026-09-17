import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// §14.5 M15: `web` and `admin` are two containers from the *same* image,
// distinguished only by the APP_MODE env var — so which route surface is
// reachable has to be an app-level check, not a build-time one. This is a
// coarse route-shape gate only (no DB/session access — keeps this runnable
// on the Edge runtime); the actual identity/allowlist/admin-email checks
// still happen per-page via requireSession/requireAdmin (lib/authz.ts),
// which is where the real security boundary is.

const ADMIN_ONLY_PREFIXES = ["/admin", "/api/admin"];
const WEB_ONLY_PREFIXES = ["/upload", "/lectures", "/settings", "/api/lectures", "/api/jobs", "/api/me"];

export function proxy(req: NextRequest) {
  const isAdminMode = process.env.APP_MODE === "admin";
  const { pathname } = req.nextUrl;

  const isAdminOnlyPath = ADMIN_ONLY_PREFIXES.some((p) => pathname.startsWith(p));
  const isWebOnlyPath = WEB_ONLY_PREFIXES.some((p) => pathname.startsWith(p));

  if (!isAdminMode && isAdminOnlyPath) {
    return NextResponse.redirect(new URL("/upload", req.url));
  }
  if (isAdminMode && isWebOnlyPath) {
    return NextResponse.redirect(new URL("/admin", req.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
