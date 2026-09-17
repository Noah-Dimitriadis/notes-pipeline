import { randomUUID } from "node:crypto";
import type { Adapter, AdapterAccount, AdapterSession, AdapterUser } from "next-auth/adapters";
import { getAuthDb } from "./auth-db";

// Auth.js v5 has no official adapter for a raw SQLite file (checked: no
// @auth/sqlite-adapter or @auth/better-sqlite3-adapter package exists on
// npm as of this build — only ORM-backed ones like @auth/drizzle-adapter).
// The Adapter interface is a small, documented, plain TS interface, and
// PLAN.md doesn't want an ORM pulled in just for this one table set, so
// this is a direct hand-written implementation against `auth.db`
// (better-sqlite3, see auth-db.ts). Only the methods OAuth + database
// sessions actually need are implemented — no email/magic-link provider
// is used, so verification-token methods are intentionally omitted.

interface UserRow {
  id: string;
  name: string | null;
  email: string;
  emailVerified: string | null;
  image: string | null;
}

interface SessionRow {
  sessionToken: string;
  userId: string;
  expires: string;
}

function rowToUser(row: UserRow): AdapterUser {
  return {
    id: row.id,
    name: row.name,
    email: row.email,
    emailVerified: row.emailVerified ? new Date(row.emailVerified) : null,
    image: row.image,
  };
}

function rowToSession(row: SessionRow): AdapterSession {
  return { sessionToken: row.sessionToken, userId: row.userId, expires: new Date(row.expires) };
}

export function SqliteAdapter(): Adapter {
  const db = () => getAuthDb();

  return {
    async createUser(user: Omit<AdapterUser, "id">) {
      const id = randomUUID();
      db()
        .prepare(
          "INSERT INTO users(id, name, email, emailVerified, image) VALUES (?, ?, ?, ?, ?)",
        )
        .run(
          id,
          user.name ?? null,
          user.email,
          user.emailVerified ? user.emailVerified.toISOString() : null,
          user.image ?? null,
        );
      return { ...user, id };
    },

    async getUser(id: string) {
      const row = db().prepare<[string], UserRow>("SELECT * FROM users WHERE id = ?").get(id);
      return row ? rowToUser(row) : null;
    },

    async getUserByEmail(email: string) {
      const row = db()
        .prepare<[string], UserRow>("SELECT * FROM users WHERE email = ?")
        .get(email);
      return row ? rowToUser(row) : null;
    },

    async getUserByAccount({ provider, providerAccountId }) {
      const row = db()
        .prepare<[string, string], UserRow>(
          `SELECT users.* FROM users
           JOIN accounts ON accounts.userId = users.id
           WHERE accounts.provider = ? AND accounts.providerAccountId = ?`,
        )
        .get(provider, providerAccountId);
      return row ? rowToUser(row) : null;
    },

    async updateUser(user: Partial<AdapterUser> & { id: string }) {
      const existing = db()
        .prepare<[string], UserRow>("SELECT * FROM users WHERE id = ?")
        .get(user.id);
      if (!existing) throw new Error(`updateUser: no such user ${user.id}`);
      const merged = { ...rowToUser(existing), ...user };
      db()
        .prepare(
          "UPDATE users SET name = ?, email = ?, emailVerified = ?, image = ? WHERE id = ?",
        )
        .run(
          merged.name ?? null,
          merged.email,
          merged.emailVerified ? merged.emailVerified.toISOString() : null,
          merged.image ?? null,
          merged.id,
        );
      return merged;
    },

    async linkAccount(account: AdapterAccount) {
      db()
        .prepare(
          `INSERT INTO accounts(
             id, userId, type, provider, providerAccountId, refresh_token,
             access_token, expires_at, token_type, scope, id_token, session_state
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .run(
          randomUUID(),
          account.userId,
          account.type,
          account.provider,
          account.providerAccountId,
          account.refresh_token ?? null,
          account.access_token ?? null,
          account.expires_at ?? null,
          account.token_type ?? null,
          account.scope ?? null,
          account.id_token ?? null,
          account.session_state ?? null,
        );
    },

    async createSession(session: { sessionToken: string; userId: string; expires: Date }) {
      db()
        .prepare("INSERT INTO sessions(id, sessionToken, userId, expires) VALUES (?, ?, ?, ?)")
        .run(randomUUID(), session.sessionToken, session.userId, session.expires.toISOString());
      return session;
    },

    async getSessionAndUser(sessionToken: string) {
      const session = db()
        .prepare<[string], SessionRow>("SELECT * FROM sessions WHERE sessionToken = ?")
        .get(sessionToken);
      if (!session) return null;
      const user = db()
        .prepare<[string], UserRow>("SELECT * FROM users WHERE id = ?")
        .get(session.userId);
      if (!user) return null;
      return { session: rowToSession(session), user: rowToUser(user) };
    },

    async updateSession(session: Partial<AdapterSession> & { sessionToken: string }) {
      const existing = db()
        .prepare<[string], SessionRow>("SELECT * FROM sessions WHERE sessionToken = ?")
        .get(session.sessionToken);
      if (!existing) return null;
      const merged = { ...rowToSession(existing), ...session };
      db()
        .prepare("UPDATE sessions SET userId = ?, expires = ? WHERE sessionToken = ?")
        .run(merged.userId, merged.expires.toISOString(), merged.sessionToken);
      return merged;
    },

    async deleteSession(sessionToken: string) {
      db().prepare("DELETE FROM sessions WHERE sessionToken = ?").run(sessionToken);
    },
  };
}

/**
 * Directly deletes every session row for a user — outside the Adapter
 * interface (it has no "delete all sessions for a user" method) because
 * disabling a friend must end their session immediately (§14.5 M15
 * acceptance: "ends within one request"), not just once their current
 * session naturally expires. Called from the `session` callback in
 * auth.ts the moment it notices the user is no longer active.
 */
export function deleteAllSessionsForUser(userId: string): void {
  getAuthDb().prepare("DELETE FROM sessions WHERE userId = ?").run(userId);
}
