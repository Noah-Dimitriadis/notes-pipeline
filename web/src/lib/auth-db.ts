import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { requireEnv } from "./env";

// auth.db — Auth.js's own schema (§14.2/§14.3: "Node-only: sessions,
// accounts — Auth.js's own schema"). The Python side never touches this
// file; it lives on the `auth-db` compose volume, separate from notes.db.
//
// better-sqlite3 is synchronous and Next.js's Node.js runtime is a single
// JS thread (route handlers/server components don't run on worker threads
// the way Python's threadpool does), so one module-level connection is
// safe for concurrent requests — no connection pool needed. Cached on
// `globalThis` so Next's dev-mode module reloading doesn't reopen the file
// on every edit.

declare global {
  var __authDb: Database.Database | undefined;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT,
  email TEXT UNIQUE,
  emailVerified TEXT,
  image TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY,
  userId TEXT NOT NULL,
  type TEXT NOT NULL,
  provider TEXT NOT NULL,
  providerAccountId TEXT NOT NULL,
  refresh_token TEXT,
  access_token TEXT,
  expires_at INTEGER,
  token_type TEXT,
  scope TEXT,
  id_token TEXT,
  session_state TEXT,
  UNIQUE(provider, providerAccountId)
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  sessionToken TEXT UNIQUE NOT NULL,
  userId TEXT NOT NULL,
  expires TEXT NOT NULL
);
`;

function open(): Database.Database {
  const path = requireEnv("AUTH_DB_PATH");
  mkdirSync(dirname(path), { recursive: true });
  const db = new Database(path);
  db.pragma("journal_mode = WAL"); // matches store.py's choice for notes.db
  db.exec(SCHEMA);
  return db;
}

export function getAuthDb(): Database.Database {
  if (!globalThis.__authDb) {
    globalThis.__authDb = open();
  }
  return globalThis.__authDb;
}
