import Database from "better-sqlite3";
import { requireEnv } from "./env";

// notes.db's `users` table (§14.3) — the one table Python (store.py) and
// this app both read/write directly. Python creates/migrates the schema;
// this module only ever opens the existing file (see the `USERS_SCHEMA`
// comment in store.py — it should already exist by the time this app
// runs). Never select/write encrypted_anthropic_key or key_nonce here —
// only Python's /api/me/anthropic-key route ever touches those.

declare global {
  var __notesDb: Database.Database | undefined;
}

function open(): Database.Database {
  const path = requireEnv("DB_PATH");
  const db = new Database(path, { fileMustExist: true });
  db.pragma("journal_mode = WAL");
  return db;
}

function getDb(): Database.Database {
  if (!globalThis.__notesDb) {
    globalThis.__notesDb = open();
  }
  return globalThis.__notesDb;
}

export interface AppUser {
  email: string;
  disabled: boolean;
  is_admin: boolean;
  has_key: boolean;
  created_at: string;
}

interface UsersRow {
  email: string;
  disabled: number;
  is_admin: number;
  encrypted_anthropic_key: string | null;
  created_at: string;
}

function toAppUser(row: UsersRow): AppUser {
  return {
    email: row.email,
    disabled: !!row.disabled,
    is_admin: !!row.is_admin,
    has_key: row.encrypted_anthropic_key !== null,
    created_at: row.created_at,
  };
}

/** The one question both the signIn and session callbacks ask (§14.3). */
export function isActiveUser(email: string): boolean {
  const row = getDb()
    .prepare<[string], { disabled: number }>("SELECT disabled FROM users WHERE email = ?")
    .get(email);
  return row !== undefined && !row.disabled;
}

export function listUsers(): AppUser[] {
  const rows = getDb()
    .prepare<[], UsersRow>(
      "SELECT email, disabled, is_admin, encrypted_anthropic_key, created_at FROM users ORDER BY created_at",
    )
    .all();
  return rows.map(toAppUser);
}

/** No-op if the email already has a row — must not reset an existing
 * user's disabled/key state (mirrors store.py's `add_user`). */
export function addUser(email: string): void {
  getDb()
    .prepare(
      "INSERT INTO users(email, disabled, is_admin, created_at) VALUES (?, 0, 0, ?) ON CONFLICT(email) DO NOTHING",
    )
    .run(email, new Date().toISOString());
}

export function setUserDisabled(email: string, disabled: boolean): void {
  getDb().prepare("UPDATE users SET disabled = ? WHERE email = ?").run(disabled ? 1 : 0, email);
}
