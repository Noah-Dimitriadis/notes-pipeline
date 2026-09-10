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
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- lectures ---------------------------------------------------------

    def add_lecture(self, lecture: Lecture) -> None:
        self._conn.execute(
            """
            INSERT INTO lectures(id, course, number, title, date, dir, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                course=excluded.course, number=excluded.number,
                title=excluded.title, date=excluded.date, dir=excluded.dir
            """,
            (
                lecture.id,
                lecture.course,
                lecture.number,
                lecture.title,
                lecture.date.isoformat() if lecture.date else None,
                str(lecture.dir),
                _now(),
            ),
        )
        self._conn.commit()

    def get_lecture(self, lecture_id: str) -> Lecture | None:
        row = self._conn.execute("SELECT * FROM lectures WHERE id = ?", (lecture_id,)).fetchone()
        return _row_to_lecture(row) if row else None

    def list_lectures(self, course: str | None = None) -> list[Lecture]:
        if course is None:
            rows = self._conn.execute("SELECT * FROM lectures ORDER BY course, number").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM lectures WHERE course = ? ORDER BY number", (course,)
            ).fetchall()
        return [_row_to_lecture(row) for row in rows]

    # -- notes --------------------------------------------------------------

    def add_note(self, lecture_id: str, path: Path, model: str, prompt_version: str) -> None:
        self._conn.execute(
            "INSERT INTO notes(lecture_id, path, generated_at, model, prompt_version) VALUES (?, ?, ?, ?, ?)",
            (lecture_id, str(path), _now(), model, prompt_version),
        )
        self._conn.commit()

    def list_notes(self, lecture_id: str) -> list[dict[str, str]]:
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE lecture_id = ? ORDER BY generated_at", (lecture_id,)
        ).fetchall()
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
