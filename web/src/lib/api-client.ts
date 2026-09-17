import { isAuthEnabled, requireEnv } from "./env";
import { signInternalToken } from "./internal-auth";

/** Base URL for the `api` service, reachable only over the Docker-internal
 * network (§14.2) — never the public internet. */
export function apiBaseUrl(): string {
  return process.env.API_INTERNAL_URL ?? "http://api:8000";
}

/** A fresh internal_auth token, minted only after the caller has already
 * verified an active Auth.js session for `email` — never call this with a
 * client-supplied email. 60s validity server-side (internal_auth.py), so
 * mint immediately before each call rather than caching it.
 *
 * With AUTH_ENABLED=false, api's require_internal_user ignores this header
 * entirely (its own bypass), so INTERNAL_API_SECRET doesn't need to be set
 * at all here — sending no Authorization header keeps that true rather
 * than requiring a value that would never actually be checked. */
export function internalAuthHeaders(email: string): HeadersInit {
  if (!isAuthEnabled()) return {};
  const secret = requireEnv("INTERNAL_API_SECRET");
  return { Authorization: `Bearer ${signInternalToken(email, secret)}` };
}
