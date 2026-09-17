import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { deleteAllSessionsForUser, SqliteAdapter } from "./lib/sqlite-adapter";
import { isActiveUser } from "./lib/notes-db";

// §14.1/§14.3: database sessions, not JWT — "must be able to instantly
// revoke a friend's access by disabling their row." The allowlist decision
// itself is never made against Auth.js's own `users` table (that one just
// tracks who has ever completed Google sign-in); it's always against
// notes.db's `users` table, the one table both this app and the Python
// `api` service treat as the source of truth (§14.3).

export const { handlers, auth, signIn, signOut } = NextAuth({
  adapter: SqliteAdapter(),
  session: { strategy: "database" },
  // Explicit clientId/clientSecret (rather than Auth.js's AUTH_GOOGLE_ID/
  // AUTH_GOOGLE_SECRET convention) so this reuses the same GOOGLE_CLIENT_ID/
  // GOOGLE_CLIENT_SECRET env vars the Python api service already reads
  // (web_config.py) for the same OAuth client, instead of needing two
  // differently-named copies of one secret.
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  ],
  // Reached only through goosenest02's reverse proxy (§14.2/M11), never
  // directly — Auth.js's host-header check needs to trust that proxy.
  trustHost: true,
  pages: { signIn: "/login" },
  callbacks: {
    // Runs before any adapter write — returning false here means Auth.js
    // never creates a user/account/session row for a rejected sign-in
    // (§14.1: "explicit per-email allowlist, not open signup" — no
    // auto-created row on first Google login).
    async signIn({ user }) {
      if (!user.email) return false;
      return isActiveUser(user.email);
    },
    // Runs on every `auth()` call (database strategy re-reads the session
    // row + calls this callback per request — no long-lived JWT to go
    // stale). Re-checking `disabled` here, not just at sign-in, is what
    // makes an admin's disable take effect within one request rather than
    // waiting for a JWT to expire.
    async session({ session, user }) {
      const active = isActiveUser(user.email);
      if (!active) {
        // Kill every session row for this user now, so the very next
        // request has no valid session to read at all — not just this one.
        deleteAllSessionsForUser(user.id);
      }
      return {
        ...session,
        user: { ...session.user, id: user.id, email: user.email, disabled: !active },
      };
    },
  },
});
