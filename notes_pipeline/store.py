from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .models import Lecture

SCHEMA = """
CREATE TABLE IF NOT EXISTS lectures(
    id TEXT PRIMARY KEY, course TEXT, number INT,
    title TEXT, date TEXT, dir TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS stage_cache(
    key TEXT PRIMARY KEY, stage TEXT, lecture_id TEXT,
    payload_path TEXT, meta TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS notes(
    lecture_id TEXT, path TEXT, generated_at TEXT,
    model TEXT, prompt_version TEXT
);
"""

# M12 (§14.3) — the hosted multi-tenant service's one addition to this
# schema. `users` is hand-written (no ORM, no migration framework) on
# EITHER side deliberately: it's the one place Python (this store) and the
# Node web app touch the same rows, so the shape needs to stay stable.
USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    email TEXT PRIMARY KEY,
    disabled INTEGER NOT NULL DEFAULT 0,
    is_admin INTEGER NOT NULL DEFAULT 0,
    encrypted_anthropic_key TEXT,
    key_nonce TEXT,
    created_at TEXT NOT NULL
);
"""

# Columns added after the personal (M0-M9) schema was already in use —
# SQLite has no `ADD COLUMN IF NOT EXISTS`, so these are applied by
# inspecting `PRAGMA table_info` instead (see `_migrate`).
_USER_ID_COLUMNS = {"lectures": "user_id", "notes": "user_id"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_lecture(row: sqlite3.Row) -> Lecture:
    return Lecture(
        id=row["id"],
        course=row["course"],
        number=row["number"],
        title=row["title"],
        date=date.fromisoformat(row["date"]) if row["date"] else None,
        dir=Path(row["dir"]),
    )


class Store:
    """SQLite-backed metadata store. Stage payloads live on disk as files;
    this only tracks lecture records, cache-entry pointers, and note history."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        # §14.1: "SQLite, shared Docker volume, WAL mode" — the hosted
        # service (M13) opens a fresh Store per request across several
        # threads (FastAPI's threadpool, the build worker), all against the
        # same file; WAL mode is what lets those readers/writers not block
        # each other. Harmless for the personal single-process CLI/MCP too.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.executescript(USERS_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        for table, column in _USER_ID_COLUMNS.items():
            existing = {
                row["name"]
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- lectures ---------------------------------------------------------

    def add_lecture(self, lecture: Lecture, *, user_id: str | None = None) -> None:
        self._conn.execute(
            """
            INSERT INTO lectures(id, course, number, title, date, dir, created_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                course=excluded.course, number=excluded.number,
                title=excluded.title, date=excluded.date, dir=excluded.dir,
                user_id=excluded.user_id
            """,
            (
                lecture.id,
                lecture.course,
                lecture.number,
                lecture.title,
                lecture.date.isoformat() if lecture.date else None,
                str(lecture.dir),
                _now(),
                user_id,
            ),
        )
        self._conn.commit()

    def get_lecture(self, lecture_id: str) -> Lecture | None:
        row = self._conn.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
        return _row_to_lecture(row) if row else None

    def get_lecture_owner(self, lecture_id: str) -> str | None:
        """The `user_id` a lecture row was created under, or None for a
        personal-library lecture (M0-M9 never sets one). Used by the hosted
        service (M13) to check ownership without adding `user_id` to the
        shared `Lecture` model that M0-M9 also depends on."""
        row = self._conn.execute(
            "SELECT user_id FROM lectures WHERE id = ?", (lecture_id,)
        ).fetchone()
        return row["user_id"] if row else None

    def list_lectures(self, course: str | None = None, *, user_id: str | None = None) -> list[Lecture]:
        clauses, params = [], []
        if course is not None:
            clauses.append("course = ?")
            params.append(course)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM lectures {where} ORDER BY course, number", params
        ).fetchall()
        return [_row_to_lecture(row) for row in rows]

    # -- notes --------------------------------------------------------------

    def add_note(
        self, lecture_id: str, path: Path, model: str, prompt_version: str, *, user_id: str | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO notes(lecture_id, path, generated_at, model, prompt_version, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lecture_id, str(path), _now(), model, prompt_version, user_id),
        )
        self._conn.commit()

    def list_notes(self, lecture_id: str) -> list[dict[str, str]]:
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE lecture_id = ? ORDER BY generated_at", (lecture_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    # -- users (M12, §14.3) ---------------------------------------------------
    # Hand-written, no ORM: this table is also read/written directly by the
    # Node `web`/`admin` app (M14/M15), so its shape must stay exactly what
    # §14.3 specifies.

    def get_user(self, email: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def is_active_user(self, email: str) -> bool:
        """True iff `email` has a row in `users` and is not disabled. This is
        the one question both the web app's Auth.js `signIn` callback and
        the MCP token verifier (M13) ask against this same table."""
        row = self._conn.execute(
            "SELECT disabled FROM users WHERE email = ?", (email,)
        ).fetchone()
        return row is not None and not row["disabled"]

    def add_user(self, email: str, *, is_admin: bool = False) -> None:
        """Insert a new allowlisted user. A no-op if the email already has a
        row (admin's "add-by-email" must not silently reset an existing
        user's disabled/key state)."""
        self._conn.execute(
            "INSERT INTO users(email, disabled, is_admin, created_at) VALUES (?, 0, ?, ?) "
            "ON CONFLICT(email) DO NOTHING",
            (email, int(is_admin), _now()),
        )
        self._conn.commit()

    def set_user_disabled(self, email: str, disabled: bool) -> None:
        self._conn.execute(
            "UPDATE users SET disabled = ? WHERE email = ?", (int(disabled), email)
        )
        self._conn.commit()

    def set_user_key(self, email: str, *, ciphertext: str, nonce: str) -> None:
        self._conn.execute(
            "UPDATE users SET encrypted_anthropic_key = ?, key_nonce = ? WHERE email = ?",
            (ciphertext, nonce, email),
        )
        self._conn.commit()

    def list_users(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    # -- stage cache (used by cache.Cache) -----------------------------------

    def get_cache_entry(self, key: str) -> Path | None:
        row = self._conn.execute(
            "SELECT payload_path FROM stage_cache WHERE key = ?", (key,)
        ).fetchone()
        return Path(row["payload_path"]) if row else None

    def put_cache_entry(
        self, key: str, *, stage: str, lecture_id: str, payload_path: Path, meta: dict
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO stage_cache(key, stage, lecture_id, payload_path, meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                stage=excluded.stage, lecture_id=excluded.lecture_id,
                payload_path=excluded.payload_path, meta=excluded.meta
            """,
            (key, stage, lecture_id, str(payload_path), json.dumps(meta), _now()),
        )
        self._conn.commit()
