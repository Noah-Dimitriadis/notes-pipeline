from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

from . import library, pipeline
from .config import Config, load_config
from .store import Store

mcp = FastMCP("notes-pipeline")

# Loaded once at process start — the MCP server is a long-lived subprocess,
# unlike the CLI which reloads config on every invocation.
_cfg: Config = load_config()


# -- background jobs ----------------------------------------------------------
#
# A full build takes minutes (transcription alone is ~6 min uncached), far
# too long for a synchronous MCP tool call. build_lecture() starts a job on
# a background thread and returns immediately; job_status() is polled for
# progress until the job finishes. Jobs live only in this process's memory —
# fine, since the MCP server is a subprocess of one Claude Code session.


@dataclass
class Job:
    id: str
    course: str
    number: int
    status: str = "running"  # running | done | error
    stage: Optional[str] = None
    message: str = ""
    progress: Optional[float] = None
    result_path: Optional[str] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def snapshot(self) -> dict:
        return {
            "job_id": self.id,
            "course": self.course,
            "number": self.number,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "result_path": self.result_path,
            "error": self.error,
            "elapsed_seconds": round((self.finished_at or time.time()) - self.started_at, 1),
        }


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


class JobReporter:
    """pipeline.Reporter that updates a Job's state instead of printing to
    a terminal, so job_status() has something live to report."""

    def __init__(self, job: Job):
        self._job = job

    def _update(self, **kwargs: object) -> None:
        with _jobs_lock:
            for key, value in kwargs.items():
                setattr(self._job, key, value)

    def stage_starting(self, name: str, message: str) -> None:
        self._update(stage=name, message=message, progress=None)

    def stage_skipped(self, name: str, message: str) -> None:
        self._update(stage=name, message=message)

    def stage_done(self, name: str, description: str, elapsed: Optional[float]) -> None:
        suffix = "cached" if elapsed is None else f"{elapsed:.1f}s"
        self._update(stage=name, message=f"{description} ({suffix})", progress=None)

    def transcribe_progress(self, fraction: float, total_duration: float) -> None:
        self._update(progress=fraction)

    def synthesize_progress(self, phase: str, chars: int) -> None:
        self._update(message=f"{phase} ({chars:,} chars)")

    def emit_done(self, path: Path) -> None:
        self._update(stage="emit", message=str(path), progress=1.0)


def _run_job(job: Job, *, audio: Path, deck: Optional[Path], notes: Optional[Path],
             out: Path, course_code: str, number: int, course_name: Optional[str],
             instructor: Optional[str], force: bool) -> None:
    try:
        result = pipeline.run_build(
            audio=audio, deck=deck, notes=notes, assets=[], out=out,
            course_code=course_code, number=number,
            course_name=course_name, instructor=instructor,
            force=force, no_cache=False, cfg=_cfg,
            reporter=JobReporter(job),
        )
        with _jobs_lock:
            job.status = "done"
            job.result_path = str(result)
            job.finished_at = time.time()
    except Exception as exc:  # noqa: BLE001 - reported through job_status, not raised
        with _jobs_lock:
            job.status = "error"
            job.error = f"{exc}\n{traceback.format_exc()}"
            job.finished_at = time.time()


# -- tools ----------------------------------------------------------------


@mcp.tool()
def list_courses() -> list[dict]:
    """List every course known to the library (has a course.toml)."""
    return [
        {
            "code": info.get("code"),
            "name": info.get("name"),
            "instructor": info.get("instructor"),
            "root": str(course_root),
        }
        for course_root, info in library.list_courses(_cfg.library_root)
    ]


@mcp.tool()
def list_lectures(course: Optional[str] = None) -> list[dict]:
    """List lectures for a course (or every course, if omitted), each
    marked whether it has been built yet."""
    store = Store(_cfg.db_path)
    results: list[dict] = []
    for course_root, info in library.list_courses(_cfg.library_root):
        code = info.get("code", course_root.name)
        if course is not None and code.lower() != course.lower():
            continue
        pattern = info.get("pattern", "Week{n}")
        built = {lec.number: lec for lec in store.list_lectures(code)}
        numbers = sorted(set(library.list_lecture_numbers(course_root, pattern)) | set(built))
        for number in numbers:
            lecture = built.get(number)
            notes = store.list_notes(f"{code}-{number}")
            results.append({
                "course": code,
                "number": number,
                "built": lecture is not None,
                "title": lecture.title if lecture else None,
                "notes_path": notes[-1]["path"] if notes else None,
            })
    return results


@mcp.tool()
def build_lecture(course: str, number: int, force: bool = False) -> dict:
    """Start building a lecture's notes in the background (audio -> slides ->
    transcribe -> synthesize -> emit). Returns immediately with a job_id;
    poll job_status(job_id) for progress, since a full build takes minutes."""
    course_root, info = library.find_course(_cfg.library_root, course)
    pattern = info.get("pattern", "Week{n}")
    audio, deck, notes, out = library.resolve_course_paths(course_root, pattern, number)
    code = info.get("code", course)

    job = Job(id=uuid.uuid4().hex, course=code, number=number)
    with _jobs_lock:
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_job,
        kwargs=dict(
            job=job, audio=audio, deck=deck, notes=notes, out=out,
            course_code=code, number=number,
            course_name=info.get("name"), instructor=info.get("instructor"),
            force=force,
        ),
        daemon=True,
    )
    thread.start()
    return {"job_id": job.id}


@mcp.tool()
def job_status(job_id: str) -> dict:
    """Check on a build_lecture job: current stage, progress, and the
    result path or error once it finishes."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return {"error": f"No such job: {job_id!r}"}
    return job.snapshot()


@mcp.tool()
def get_notes(course: str, number: int) -> str:
    """Return the generated markdown notes for a lecture. Fails clearly if
    that lecture hasn't been built yet — call build_lecture first."""
    store = Store(_cfg.db_path)
    lecture_id = f"{course}-{number}"
    notes = store.list_notes(lecture_id)
    if not notes:
        raise ValueError(
            f"No notes found for {lecture_id}. Build it first with build_lecture."
        )
    path = Path(notes[-1]["path"])
    if not path.exists():
        raise ValueError(f"Notes record exists for {lecture_id} but the file is missing: {path}")
    return path.read_text(encoding="utf-8")


@mcp.tool()
def search_notes(query: str, course: Optional[str] = None) -> list[dict]:
    """Case-insensitive search across generated lecture notes for `query`,
    returning matching lectures with a short snippet of context."""
    store = Store(_cfg.db_path)
    needle = query.lower()
    results: list[dict] = []
    for lecture in store.list_lectures(course):
        notes = store.list_notes(lecture.id)
        if not notes:
            continue
        path = Path(notes[-1]["path"])
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        idx = lower.find(needle)
        if idx == -1:
            continue
        start = max(0, idx - 80)
        end = min(len(text), idx + len(query) + 80)
        snippet = text[start:end].replace("\n", " ").strip()
        results.append({
            "course": lecture.course,
            "number": lecture.number,
            "lecture_id": lecture.id,
            "path": str(path),
            "snippet": snippet,
        })
    return results


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
