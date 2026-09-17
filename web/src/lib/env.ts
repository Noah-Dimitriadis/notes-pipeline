// Small env accessor with a clear failure instead of `undefined` leaking
// into a header or SQL query somewhere downstream.
export function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required env var: ${name}`);
  return value;
}

export function optionalEnv(name: string, fallback: string): string {
  return process.env[name] ?? fallback;
}

// Mirrors pydantic-settings' bool coercion for AUTH_ENABLED (web_config.py)
// closely enough that the same value in deploy/.env means the same thing
// on both sides — default true (fail closed on a forgotten/typo'd value).
const FALSY = new Set(["0", "false", "no", "off", "f", "n"]);

export function isAuthEnabled(): boolean {
  const value = process.env.AUTH_ENABLED;
  return value === undefined || !FALSY.has(value.trim().toLowerCase());
}

// The one identity every request runs as when AUTH_ENABLED=false. Must
// match notes_pipeline/web_config.py's DEV_BYPASS_EMAIL exactly — kept in
// sync by convention (no shared import across the language boundary), same
// as internal-auth.ts's token format.
export const DEV_BYPASS_EMAIL = "dev-local@notes-pipeline.local";
