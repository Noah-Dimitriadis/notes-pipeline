"""M13 — the `api` service (§14.2, §14.5): multi-tenant FastAPI + a
GoogleProvider-gated FastMCP mounted at `/mcp`. Run with:

    uvicorn notes_pipeline.webapi:app --host 0.0.0.0 --port 8000

(`deploy/api/Dockerfile`'s CMD does exactly this once this module exists —
see that file's comment.)

Two trust boundaries meet in this one process:

- `/api/*` — internal only, never reaches goosenest02's Ingress (M11 routes
  only `/` and `/mcp` there). Called exclusively by the `web` Next.js app
  (M14) over the Docker-internal network. `web` authenticates the browser
  itself (Auth.js); it asserts *which* user a request is for by minting a
  short-lived `internal_auth` token after checking its own session — never
  a bare client-supplied id (see `internal_auth.py`'s module docstring for
  why, and `require_internal_user` below for the check).
- `/mcp` — reaches the public internet through M11's Ingress. Protected by
  Google OAuth (`GoogleProvider`) plus an allowlist check against the same
  `users` table `/api/*` and `web` both use (`AllowlistTokenVerifier`).

One global build-worker thread drains a FIFO queue — §14.1: "One GPU —
concurrent whisper runs would just contend with each other, not go
faster." A per-user pending-job cap keeps one account from starving the
queue for everyone else (§14.1, §M16 checklist).
"""

from __future__ import annotations

import hashlib
import queue
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.utilities.lifespan import combine_lifespans

from . import pipeline
from .config import Config, load_config
from .crypto import KeyEncryptionError, decrypt_key, encrypt_key
from .internal_auth import InternalAuthError, verify_internal_token
from .models import Lecture
from .store import Store
from .upload_tokens import UploadTokenError, sign_upload_token, verify_upload_token
from .web_config import DEV_BYPASS_EMAIL, WebConfig, load_web_config

_cfg: Config = load_config()
_web_cfg: WebConfig = load_web_config()


def _db() -> Store:
    """A fresh Store per call, deliberately — sqlite3 connections aren't
    shareable across threads, and this module's route handlers, MCP tools,
    and the build worker each run on different ones (FastAPI's threadpool
    for sync routes, the worker thread, FastMCP's own request handling).
    Cheap: opening a local sqlite file is not the bottleneck here."""
    return Store(_web_cfg.db_path)


if not _web_cfg.auth_enabled:
    # So every other check downstream of _require_email/require_internal_user
    # (key-on-file, ownership, "is this an active user") works unchanged in
    # local-testing mode without special-casing DEV_BYPASS_EMAIL anywhere
    # else — it's just a normal, always-active row.
    _db().add_user(DEV_BYPASS_EMAIL)


# -- allowlisted Google auth (§14.1, M13) -------------------------------------


class AllowlistTokenVerifier(TokenVerifier):
    """Wraps another TokenVerifier (Google's own) and additionally rejects
    any token whose verified `claims["email"]` isn't an active row in
    `users` — the actual access-control decision, on top of Google merely
    proving *who* the caller is."""

    def __init__(self, inner: TokenVerifier, db_path: Path):
        super().__init__(required_scopes=inner.required_scopes)
        self._inner = inner
        self._db_path = db_path

    async def verify_token(self, token: str) -> AccessToken | None:
        access_token = await self._inner.verify_token(token)
        if access_token is None:
            return None
        email = access_token.claims.get("email")
        if not email or not Store(self._db_path).is_active_user(email):
            return None
        return access_token


class AllowlistedGoogleProvider(GoogleProvider):
    """`GoogleProvider` builds its own internal `GoogleTokenVerifier` and
    doesn't accept one as a constructor argument, so the allowlist check is
    spliced in afterwards by wrapping the `_token_validator` attribute
    `OAuthProxy.__init__` sets — confirmed directly against the installed
    fastmcp 4.0.3 source (`OAuthProxy._token_validator` is the single
    attribute every verification path in that class reads)."""

    def __init__(self, *, db_path: Path, **kwargs: object):
        super().__init__(**kwargs)
        self._token_validator = AllowlistTokenVerifier(self._token_validator, db_path)  # type: ignore[attr-defined]


def _require_email() -> str:
    """The verified email of the caller of the current MCP tool. Only
    reachable at all once `AllowlistTokenVerifier` has already accepted the
    token, so this email is guaranteed to be an active user — except in
    local-testing mode (`AUTH_ENABLED=false`), where there is no token at
    all and every call is DEV_BYPASS_EMAIL."""
    if not _web_cfg.auth_enabled:
        return DEV_BYPASS_EMAIL
    access_token = get_access_token()
    if access_token is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    email = access_token.claims.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Token has no verified email.")
    return email


# With AUTH_ENABLED=false, `/mcp` is mounted with no auth provider at all —
# open to anything that can reach it — rather than a fake always-accept
# TokenVerifier, so a misconfigured verifier can never be mistaken for a
# real one. Fine for local testing (the whole point), never for a real
# deployment; `WebConfig` already refuses to load with auth enabled and
# Google credentials missing, so this branch and a real public Ingress
# can't coexist by accident.
mcp = (
    FastMCP(
        "notes-pipeline",
        auth=AllowlistedGoogleProvider(
            db_path=_web_cfg.db_path,
            client_id=_web_cfg.google_client_id,
            client_secret=_web_cfg.google_client_secret.get_secret_value() if _web_cfg.google_client_secret else None,
            base_url=_web_cfg.mcp_base_url,
            required_scopes=["openid", "email"],
        ),
    )
    if _web_cfg.auth_enabled
    else FastMCP("notes-pipeline")
)


# -- internal auth for /api/* (web -> api only) -------------------------------


def require_internal_user(authorization: str | None = Header(default=None)) -> str:
    if not _web_cfg.auth_enabled:
        return DEV_BYPASS_EMAIL
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing internal bearer token.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        email = verify_internal_token(token, _web_cfg.internal_api_secret)
    except InternalAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    if not _db().is_active_user(email):
        raise HTTPException(status_code=403, detail="User is not active.")
    return email


# -- build jobs: one global FIFO worker across all users ----------------------


@dataclass
class Job:
    id: str
    user_id: str  # email
    lecture_id: str
    status: str = "queued"  # awaiting_upload (M17) | queued | running | done | error
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
            "lecture_id": self.lecture_id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "result_path": self.result_path,
            "error": self.error,
            "started_at": self.started_at,
            "elapsed_seconds": round((self.finished_at or time.time()) - self.started_at, 1),
        }

    def row(self) -> dict:
        """Shape `Store.upsert_job` expects — distinct from `snapshot()`
        (the API/MCP response shape) because the two audiences need
        different keys (`id` for a SQL upsert vs. `job_id` for JSON)."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "lecture_id": self.lecture_id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "result_path": self.result_path,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _row_snapshot(row: dict) -> dict:
    """`Job.snapshot()`'s shape, built from a persisted `jobs` row instead
    of a live `Job` — what `/api/jobs*` returns once a job is no longer (or
    never was, after a restart) in the in-memory `_jobs` dict."""
    started, finished = row["started_at"], row["finished_at"]
    return {
        "job_id": row["id"],
        "lecture_id": row["lecture_id"],
        "status": row["status"],
        "stage": row["stage"],
        "message": row["message"],
        "progress": row["progress"],
        "result_path": row["result_path"],
        "error": row["error"],
        "started_at": started,
        "elapsed_seconds": round((finished or time.time()) - started, 1),
    }


@dataclass
class BuildTask:
    """One queued build. Holds no API key — the worker decrypts the
    owner's key when the task starts, so a task can be persisted
    (`to_spec`) and re-queued after a restart without ever writing a
    plaintext key anywhere."""

    job: Job
    audio: list[Path]
    deck: list[Path]
    notes: Optional[Path]
    assets: list[Path]
    out: Path
    course_code: str
    number: int
    title: str
    lecture_id: str
    attempts: int = 0

    def to_spec(self) -> dict:
        return {
            "audio": [str(p) for p in self.audio],
            "deck": [str(p) for p in self.deck],
            "notes": str(self.notes) if self.notes else None,
            "assets": [str(p) for p in self.assets],
            "out": str(self.out),
            "course_code": self.course_code,
            "number": self.number,
            "title": self.title,
            "lecture_id": self.lecture_id,
            "attempts": self.attempts,
        }

    @classmethod
    def from_spec(cls, job: Job, spec: dict) -> "BuildTask":
        return cls(
            job=job,
            audio=[Path(p) for p in spec["audio"]],
            deck=[Path(p) for p in spec["deck"]],
            notes=Path(spec["notes"]) if spec["notes"] else None,
            assets=[Path(p) for p in spec["assets"]],
            out=Path(spec["out"]),
            course_code=spec["course_code"],
            number=spec["number"],
            title=spec["title"],
            lecture_id=spec["lecture_id"],
            attempts=spec.get("attempts", 0),
        )


class JobReporter:
    """pipeline.Reporter that updates a Job's state. Deliberately not
    shared with mcp_server.JobReporter — same shape, but that class closes
    over mcp_server's own module-level lock and job dict, which have
    nothing to do with this service's.

    Also writes each update through to `store` (persistent job history,
    §"job history persistent" ask) — `store` is the worker thread's own
    long-lived Store (see `_worker_loop`), never shared across threads."""

    def __init__(self, job: Job, lock: threading.Lock, store: Store):
        self._job = job
        self._lock = lock
        self._store = store

    def _update(self, **kwargs: object) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._job, key, value)
            self._store.upsert_job(self._job.row())

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
        # Not `str(path)` — that's a location inside the api container's
        # volume, meaningless (and layout-breaking) to a browser user.
        self._update(stage="emit", message="Notes ready", progress=1.0)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_pending_counts: dict[str, int] = {}
_build_queue: "queue.Queue[BuildTask | None]" = queue.Queue()
_worker_thread: Optional[threading.Thread] = None


@dataclass
class UploadSlot:
    """One file an agent still owes a `create_build` job (M17)."""

    name: str  # as the caller named it — echoed back so it can match URLs to files
    kind: str  # audio | deck | notes | asset
    path: Path
    state: str = "waiting"  # waiting | receiving | received


@dataclass
class PendingBuild:
    task: BuildTask
    slots: list[UploadSlot]
    expires_at: float


# Builds created by `create_build` whose files haven't all arrived yet, by
# job id. In memory only: a restart closes these out as errors (see
# `_recover_interrupted_jobs`) — the agent just calls `create_build` again.
_pending_builds: dict[str, PendingBuild] = {}

# A job interrupted by a restart is re-queued at most this many total
# starts, so a build that itself takes the process down (OOM mid-whisper)
# can't crash-loop the service.
_MAX_ATTEMPTS = 2


def _pending_count(user_id: str) -> int:
    with _jobs_lock:
        return _pending_counts.get(user_id, 0)


def _enqueue(task: BuildTask) -> None:
    """Persist the task's inputs (so a restart can re-queue it) and hand it
    to the worker. The job row must already exist; the caller has already
    counted the job against the user's pending cap."""
    _db().set_job_spec(task.job.id, task.to_spec())
    _build_queue.put(task)


def _worker_loop() -> None:
    # One Store for the whole life of this thread — sqlite3 connections
    # aren't shareable *across* threads, but reusing one *within* this
    # single dedicated thread (rather than opening a fresh one per job, or
    # per JobReporter update) is fine and avoids needless file opens on
    # every progress tick.
    store = Store(_web_cfg.db_path)
    while True:
        task = _build_queue.get()
        if task is None:  # shutdown signal
            _build_queue.task_done()
            break
        job = task.job
        task.attempts += 1
        store.set_job_spec(job.id, task.to_spec())
        with _jobs_lock:
            job.status = "running"
            store.upsert_job(job.row())
        try:
            result = pipeline.run_build(
                audio=task.audio,
                deck=task.deck,
                notes=task.notes,
                assets=task.assets,
                out=task.out,
                course_code=task.course_code,
                number=task.number,
                course_name=None,
                instructor=None,
                title=task.title,
                force=False,
                no_cache=False,
                cfg=_cfg,
                reporter=JobReporter(job, _jobs_lock, store),
                api_key=_user_api_key(job.user_id),
                user_id=job.user_id,
                lecture_id=task.lecture_id,
            )
            with _jobs_lock:
                job.status = "done"
                job.result_path = str(result)
                job.finished_at = time.time()
                store.upsert_job(job.row())
        except Exception as exc:  # noqa: BLE001 - reported through job_status
            with _jobs_lock:
                job.status = "error"
                job.error = f"{exc}\n{traceback.format_exc()}"
                job.finished_at = time.time()
                store.upsert_job(job.row())
        finally:
            with _jobs_lock:
                _pending_counts[job.user_id] = max(0, _pending_counts.get(job.user_id, 1) - 1)
            _build_queue.task_done()


def _recover_interrupted_jobs() -> None:
    """A job still unfinished in the table at startup belonged to a process
    that's gone now (crash, redeploy). Re-queue the ones that can be — their
    inputs are still on disk and every finished stage is cached, so a rerun
    resumes rather than starts over — and close the rest out as errors
    rather than let them sit there looking like they're still going."""
    store = _db()
    for row in store.list_interrupted_jobs():
        spec = row["spec"]
        job = Job(
            id=row["id"], user_id=row["user_id"], lecture_id=row["lecture_id"],
            stage=row["stage"], message=row["message"] or "", started_at=row["started_at"],
        )
        if row["status"] != "awaiting_upload" and spec is not None and spec.get("attempts", 0) < _MAX_ATTEMPTS:
            job.status, job.stage, job.message = "queued", None, "Re-queued after a server restart."
            with _jobs_lock:
                _jobs[job.id] = job
                _pending_counts[job.user_id] = _pending_counts.get(job.user_id, 0) + 1
            store.upsert_job(job.row())
            _build_queue.put(BuildTask.from_spec(job, spec))
            continue
        if row["status"] == "awaiting_upload":
            job.error = "Server restarted before every file was uploaded. Call create_build again."
        elif spec is None:
            job.error = "Interrupted by a server restart."
        else:
            job.error = f"Interrupted by a server restart {spec['attempts']} times; not retrying."
        job.status, job.finished_at = "error", time.time()
        store.upsert_job(job.row())


def _expire_pending_builds() -> None:
    now = time.time()
    with _jobs_lock:
        expired = [p for p in _pending_builds.values() if p.expires_at < now]
        for pending in expired:
            job = pending.task.job
            del _pending_builds[job.id]
            job.status = "error"
            job.error = "Upload window expired before every file arrived. Call create_build again."
            job.finished_at = now
            _pending_counts[job.user_id] = max(0, _pending_counts.get(job.user_id, 1) - 1)
    for pending in expired:
        _db().upsert_job(pending.task.job.row())


@asynccontextmanager
async def _api_lifespan(_app: FastAPI):
    global _worker_thread
    _recover_interrupted_jobs()
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    _worker_thread.start()
    try:
        yield
    finally:
        _build_queue.put(None)


# -- helpers ---------------------------------------------------------------


def _user_slug(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]


def _user_dir(email: str) -> Path:
    path = _web_cfg.uploads_root / _user_slug(email)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _lecture_number(lecture_id: str) -> int:
    # Stable, collision-safe-enough int derived from the lecture uuid, so
    # pipeline.run_build's `course_code-number` id scheme (unmodified, per
    # §14.3) can stand in for a real uuid without pipeline.py needing to
    # know hosted lectures exist at all. Web-dropbox uploads only; M17's
    # `create_build` uses real course/number ids (`_hosted_lecture_id`).
    return int(hashlib.sha256(lecture_id.encode("utf-8")).hexdigest()[:8], 16)


def _normalize_course(course: str) -> str:
    """"ECON 2P42", "econ-2p42" and "ECON2P42" are the same course."""
    code = re.sub(r"[^A-Za-z0-9]", "", course).upper()
    if not code:
        raise ValueError(f"Not a usable course code: {course!r}")
    return code


def _hosted_lecture_id(email: str, course_code: str, number: int) -> str:
    """M17 ids: deterministic per (user, course, number), so building the
    same lecture again replaces its notes instead of adding a duplicate.
    Namespaced by user so two friends' `ECON2P42-10` never collide."""
    return f"web-{_user_slug(email)[:12]}-{course_code}-{number}"


def _user_api_key(email: str) -> str:
    """The user's decrypted Anthropic key. Raises ValueError if none is on
    file; lets KeyEncryptionError through for a key that won't decrypt."""
    user = _db().get_user(email)
    if user is None or not user.get("encrypted_anthropic_key"):
        raise ValueError("No Anthropic API key on file. Add one in Settings first.")
    return decrypt_key(user["encrypted_anthropic_key"], user["key_nonce"], _web_cfg.key_master_key)


def _decrypted_key_for(email: str) -> str:
    try:
        return _user_api_key(email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except KeyEncryptionError as exc:
        raise HTTPException(status_code=500, detail=f"Could not decrypt stored API key: {exc}") from None


def _safe_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ""


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name).lstrip(".")
    return (cleaned or "file")[:100]


async def _save_upload(upload: UploadFile, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        while chunk := await upload.read(1 << 20):
            f.write(chunk)
    return dst


def _download_filename(title: str | None) -> str:
    """`<title>.md`, with anything a filesystem or a Content-Disposition
    header would choke on stripped out."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", title or "").strip(" .")
    return f"{cleaned or 'notes'}.md"


def _content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _lecture_response(lecture: Lecture) -> dict:
    notes = _db().list_notes(lecture.id)
    return {
        "lecture_id": lecture.id,
        "title": lecture.title,
        # Real values only for M17 `create_build` lectures; web-dropbox
        # uploads carry a synthetic "web" course and hashed number.
        "course": None if lecture.course == "web" else lecture.course,
        "number": None if lecture.course == "web" else lecture.number,
        "date": lecture.date.isoformat() if lecture.date else None,
        "has_notes": bool(notes),
        "notes_generated_at": notes[-1]["generated_at"] if notes else None,
    }


# -- MCP tools ----------------------------------------------------------------
#
# §14.1 originally made this surface read-only because MCP tool calls can't
# carry large binaries. M17 keeps the bytes out of the tool call instead:
# `create_build` returns signed upload URLs and the caller PUTs each file.


@mcp.tool()
def list_my_lectures() -> list[dict]:
    """List the calling user's uploaded lectures, each flagged whether
    notes have finished generating yet."""
    email = _require_email()
    return [_lecture_response(lec) for lec in _db().list_lectures(user_id=email)]


@mcp.tool()
def get_notes(lecture_id: str) -> str:
    """Return the generated markdown notes for one of the calling user's
    own lectures. Fails if the lecture doesn't belong to the caller or
    hasn't finished building."""
    email = _require_email()
    if _db().get_lecture_owner(lecture_id) != email:
        raise ValueError(f"No such lecture: {lecture_id!r}")
    notes = _db().list_notes(lecture_id)
    if not notes:
        raise ValueError(f"{lecture_id} hasn't finished building yet.")
    path = Path(notes[-1]["path"])
    if not path.exists():
        raise ValueError(f"Notes record exists for {lecture_id} but the file is missing.")
    return path.read_text(encoding="utf-8")


@mcp.tool()
def search_notes(query: str) -> list[dict]:
    """Case-insensitive search across the calling user's own generated
    lecture notes for `query`, returning matches with a short snippet."""
    email = _require_email()
    needle = query.lower()
    results: list[dict] = []
    for lecture in _db().list_lectures(user_id=email):
        notes = _db().list_notes(lecture.id)
        if not notes:
            continue
        path = Path(notes[-1]["path"])
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        idx = text.lower().find(needle)
        if idx == -1:
            continue
        start, end = max(0, idx - 80), min(len(text), idx + len(query) + 80)
        results.append({
            "lecture_id": lecture.id,
            "title": lecture.title,
            "snippet": text[start:end].replace("\n", " ").strip(),
        })
    return results


@mcp.tool()
def job_status(job_id: str) -> dict:
    """Check on a build job: status (awaiting_upload, queued, running,
    done, error), current stage, progress, and the error once it fails.
    Only visible to the user who started it."""
    email = _require_email()
    _expire_pending_builds()
    with _jobs_lock:
        job = _jobs.get(job_id)
        snapshot = job.snapshot() if job is not None and job.user_id == email else None
    if snapshot is not None:
        return snapshot
    # Not in this process's memory (finished before a restart) — the
    # persisted row still answers, same as GET /api/jobs/{id}.
    row = _db().get_job(job_id)
    if row is None or row["user_id"] != email:
        raise ValueError(f"No such job: {job_id!r}")
    return _row_snapshot(row)


def _upload_base_url() -> str:
    if _web_cfg.mcp_base_url:
        return _web_cfg.mcp_base_url.rstrip("/")
    url = get_http_request().url  # local dev only — production always sets MCP_BASE_URL
    return f"{url.scheme}://{url.netloc}/mcp"


@mcp.tool()
def create_build(
    course: str,
    number: int,
    audio: list[str],
    deck: list[str] | None = None,
    notes: str | None = None,
    assets: list[str] | None = None,
    title: str | None = None,
) -> dict:
    """Start building notes for a lecture from files on your machine. The
    files themselves are NOT sent through this call.

    Pass each input's filename or local path: `audio` in playback order
    (required; several parts are joined), `deck` slide files in order
    (.pdf/.pptx), at most one `notes` file of your own, and any extra
    `assets`. Returns `lecture_id`, `job_id`, and one upload URL per file.
    PUT each file's raw bytes to its URL, e.g. `curl -fsS -T <path> <url>`.
    The build queues itself once the last file lands; poll
    `job_status(job_id)` until it's done (a lecture takes several minutes),
    then fetch the markdown with `get_notes(lecture_id)`.

    `course` + `number` identify the lecture (e.g. "ECON 2P42", 10).
    Building the same pair again replaces that lecture's notes instead of
    adding a duplicate. Upload URLs expire after an hour."""
    email = _require_email()
    deck, assets = deck or [], assets or []
    if not audio:
        raise ValueError("At least one audio file is required.")
    if number < 0:
        raise ValueError("number must be zero or positive.")
    if any(not _safe_suffix(name) for name in [*deck, *([notes] if notes else [])]):
        raise ValueError("Every deck and notes file needs a recognisable extension (e.g. .pdf, .pptx, .md).")
    course_code = _normalize_course(course)
    _user_api_key(email)  # fail fast, before anything is created
    _expire_pending_builds()

    lecture_id = _hosted_lecture_id(email, course_code, number)
    owner = _db().get_lecture_owner(lecture_id)
    if owner is not None and owner != email:
        raise ValueError(f"Lecture id collision for {lecture_id}; refusing to overwrite another user's lecture.")
    title = title or f"{course_code} Week {number}"
    total = len(audio) + len(deck) + (1 if notes else 0) + len(assets)

    with _jobs_lock:
        if any(
            j.lecture_id == lecture_id and j.status in ("awaiting_upload", "queued", "running")
            for j in _jobs.values()
        ):
            raise ValueError(f"{lecture_id} is already uploading or building. Wait for its job to finish first.")
        if _pending_counts.get(email, 0) >= _web_cfg.max_pending_jobs_per_user:
            raise ValueError(
                f"You already have {_web_cfg.max_pending_jobs_per_user} lecture(s) uploading, queued or building."
            )
        job = Job(
            id=uuid.uuid4().hex, user_id=email, lecture_id=lecture_id,
            status="awaiting_upload", message=f"Waiting for uploads (0 of {total})",
        )
        _jobs[job.id] = job
        _pending_counts[email] = _pending_counts.get(email, 0) + 1

    lecture_dir = _user_dir(email) / lecture_id
    # Same layout as the web dropbox: inputs in their own folder, so no
    # input can ever share a path with the generated notes.md. Wiped first,
    # so a rebuild with fewer files can't pick up a stale one (safe: no
    # other job for this lecture is active, checked above).
    inputs_dir = lecture_dir / "inputs"
    shutil.rmtree(inputs_dir, ignore_errors=True)
    inputs_dir.mkdir(parents=True)
    slots = [
        *(UploadSlot(n, "audio", inputs_dir / f"audio-{i}{_safe_suffix(n) or '.m4a'}") for i, n in enumerate(audio)),
        *(UploadSlot(n, "deck", inputs_dir / f"deck-{i}{_safe_suffix(n)}") for i, n in enumerate(deck)),
        *([UploadSlot(notes, "notes", inputs_dir / f"notes{_safe_suffix(notes)}")] if notes else []),
        *(UploadSlot(n, "asset", inputs_dir / "assets" / f"{i}-{_safe_name(n)}") for i, n in enumerate(assets)),
    ]
    task = BuildTask(
        job=job,
        audio=[s.path for s in slots if s.kind == "audio"],
        deck=[s.path for s in slots if s.kind == "deck"],
        notes=next((s.path for s in slots if s.kind == "notes"), None),
        assets=[s.path for s in slots if s.kind == "asset"],
        out=lecture_dir / "notes.md",
        course_code=course_code, number=number, title=title, lecture_id=lecture_id,
    )
    expires_at = time.time() + _web_cfg.upload_window_seconds

    # Placeholder row so list_my_lectures shows it before the build ends —
    # only for a new lecture: upserting over an existing one would blank its
    # date if this upload never completes.
    if _db().get_lecture(lecture_id) is None:
        _db().add_lecture(
            Lecture(id=lecture_id, course=course_code, number=number, title=title, date=None, dir=lecture_dir),
            user_id=email,
        )
    _db().upsert_job(job.row())
    with _jobs_lock:
        _pending_builds[job.id] = PendingBuild(task=task, slots=slots, expires_at=expires_at)

    base = _upload_base_url()
    secret = _web_cfg.internal_api_secret
    return {
        "lecture_id": lecture_id,
        "job_id": job.id,
        "course": course_code,
        "number": number,
        "title": title,
        "expires_at": int(expires_at),
        "uploads": [
            {"file": s.name, "kind": s.kind, "url": f"{base}/uploads/{sign_upload_token(job.id, i, expires_at, secret)}"}
            for i, s in enumerate(slots)
        ],
        "next": "PUT each file to its url (curl -fsS -T '<local path>' '<url>'). The build queues itself after "
                "the last upload; poll job_status(job_id), then get_notes(lecture_id).",
    }


# -- FastAPI app + routes (§14.5 M13) ---------------------------------------

mcp_app = mcp.http_app(path="/", allowed_hosts=[_web_cfg.allowed_host] if _web_cfg.allowed_host else None)
app = FastAPI(title="notes-pipeline", lifespan=combine_lifespans(_api_lifespan, mcp_app.lifespan))


class SecurityHeadersMiddleware:
    """Adds HSTS and the standard hardening headers to every response
    (M16). Needed here rather than at the edge: TLS terminates at
    Cloudflare and the Ingress has no `tls:` block, so ingress-nginx never
    emits HSTS itself, and `/mcp` + discovery bypass the gateway pod that
    sets these for `web`. Pure ASGI, not `BaseHTTPMiddleware`, so MCP's
    streamed responses pass through unbuffered."""

    _HEADERS = [
        (b"strict-transport-security", b"max-age=31536000"),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *self._HEADERS]
            await send(message)

        await self.app(scope, receive, send_with_headers)


class McpSlashMiddleware:
    """Serve `/mcp` as `/mcp/` in place rather than letting Starlette's
    `redirect_slashes` 307 it. That redirect's absolute URL uses the
    request's own scheme, which is `http` here — Cloudflare Tunnel ->
    cloudflared -> ingress-nginx is plain HTTP on goosenest02, and
    ingress-nginx overwrites X-Forwarded-Proto with that — so clients got
    bounced to `http://` (M16)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(McpSlashMiddleware)


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok\n")


@app.get("/healthz/gpu")
def healthz_gpu() -> PlainTextResponse:
    try:
        result = subprocess.run(
            [str(_cfg.whisper_bin), "--help"], capture_output=True, timeout=10, text=True
        )
        body = result.stdout + result.stderr
        return PlainTextResponse(body, status_code=200 if result.returncode == 0 else 500)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim for on-box debugging
        return PlainTextResponse(str(exc), status_code=500)


@app.post("/api/lectures")
async def create_lecture(
    title: str = Form(...),
    audio: list[UploadFile] = File(...),
    deck: list[UploadFile] = File(default=[]),
    notes: UploadFile | None = File(default=None),
    assets: list[UploadFile] = File(default=[]),
    email: str = Depends(require_internal_user),
) -> dict:
    if _pending_count(email) >= _web_cfg.max_pending_jobs_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"You already have {_web_cfg.max_pending_jobs_per_user} lecture(s) queued or building.",
        )

    if not any(f.filename for f in audio):
        raise HTTPException(status_code=422, detail="At least one audio file is required.")

    _decrypted_key_for(email)  # fail fast, before writing anything to disk

    lecture_id = uuid.uuid4().hex
    number = _lecture_number(lecture_id)
    lecture_dir = _user_dir(email) / lecture_id
    # Uploads live in their own subfolder so no user-supplied file can ever
    # share a path with the generated `notes.md` (pipeline.run_build refuses
    # an output path equal to the input notes path — an uploaded `.md` used
    # to trip that).
    inputs_dir = lecture_dir / "inputs"
    # Order matters for audio and decks (concatenated chronologically), so
    # files are numbered in the order the multipart parts arrived. An empty
    # file input submits a part with no filename — skip those.
    audio_paths = [
        await _save_upload(f, inputs_dir / f"audio-{i}{Path(f.filename or '').suffix or '.m4a'}")
        for i, f in enumerate(f for f in audio if f.filename)
    ]
    deck_paths = [
        await _save_upload(f, inputs_dir / f"deck-{i}{Path(f.filename or '').suffix}")
        for i, f in enumerate(f for f in deck if f.filename)
    ]
    asset_paths = [
        await _save_upload(f, inputs_dir / "assets" / f"{i}-{Path(f.filename or '').name}")
        for i, f in enumerate(f for f in assets if f.filename)
    ]
    notes_path = (
        await _save_upload(notes, inputs_dir / f"notes{Path(notes.filename or '').suffix}")
        if notes is not None and notes.filename
        else None
    )
    out_path = lecture_dir / "notes.md"

    lecture_row_id = f"web-{number}"
    job = Job(id=uuid.uuid4().hex, user_id=email, lecture_id=lecture_row_id)
    with _jobs_lock:
        _jobs[job.id] = job
        _pending_counts[email] = _pending_counts.get(email, 0) + 1

    # The lecture row (title, id) so it shows up in GET /api/lectures even
    # before the build finishes; pipeline.run_build will upsert it again
    # once notes are emitted (same id, so this is just an early placeholder).
    _db().add_lecture(
        Lecture(id=lecture_row_id, course="web", number=number, title=title, date=None, dir=lecture_dir),
        user_id=email,
    )
    # Persist the job's `queued` row immediately, not just once the worker
    # picks it up — a job sitting in `_build_queue` behind others is real
    # history too, and this is what makes GET /api/jobs show it right away.
    _db().upsert_job(job.row())
    _enqueue(BuildTask(
        job=job, audio=audio_paths, deck=deck_paths, notes=notes_path, assets=asset_paths, out=out_path,
        course_code="web", number=number, title=title, lecture_id=lecture_row_id,
    ))
    return {"lecture_id": lecture_row_id, "job_id": job.id}


@app.get("/api/lectures")
def list_my_lectures_route(email: str = Depends(require_internal_user)) -> list[dict]:
    return [_lecture_response(lec) for lec in _db().list_lectures(user_id=email)]


@app.get("/api/lectures/{lecture_id}/notes")
def get_lecture_notes_route(lecture_id: str, email: str = Depends(require_internal_user)) -> PlainTextResponse:
    if _db().get_lecture_owner(lecture_id) != email:
        raise HTTPException(status_code=404, detail="No such lecture.")
    notes = _db().list_notes(lecture_id)
    if not notes:
        raise HTTPException(status_code=404, detail="This lecture hasn't finished building yet.")
    path = Path(notes[-1]["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Notes record exists but the file is missing.")
    lecture = _db().get_lecture(lecture_id)
    filename = _download_filename(lecture.title if lecture else None)
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@app.get("/api/jobs")
def list_my_jobs_route(email: str = Depends(require_internal_user)) -> list[dict]:
    """Persistent job history for the calling user, newest first — survives
    an `api` restart, unlike the in-memory `_jobs` dict."""
    return [_row_snapshot(row) for row in _db().list_jobs(user_id=email, limit=50)]


@app.get("/api/jobs/{job_id}")
def get_job_route(job_id: str, email: str = Depends(require_internal_user)) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is not None:
        if job.user_id != email:
            raise HTTPException(status_code=404, detail="No such job.")
        return job.snapshot()
    # Not in this process's memory — either it finished before a restart,
    # or it belongs to a request replaying an old job_id. Either way, the
    # persisted row (if any, and if it's actually this user's) still answers.
    row = _db().get_job(job_id)
    if row is None or row["user_id"] != email:
        raise HTTPException(status_code=404, detail="No such job.")
    return _row_snapshot(row)


@app.post("/api/me/anthropic-key")
def set_anthropic_key_route(payload: dict, email: str = Depends(require_internal_user)) -> dict:
    api_key = str(payload.get("api_key", "")).strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required.")
    ciphertext, nonce = encrypt_key(api_key, _web_cfg.key_master_key)
    _db().set_user_key(email, ciphertext=ciphertext, nonce=nonce)
    return {"ok": True}


# M17 — the other half of `create_build`. Under `/mcp` only because that's
# the prefix the Ingress already routes to this service (with the 2g body
# size and 300s timeouts); it's a plain route, not part of the MCP app, and
# must stay registered before the `/mcp` mount below so it matches first.
# No session/OAuth auth: the signed token in the path is the credential.
@app.put("/mcp/uploads/{token}")
async def upload_build_input(token: str, request: Request) -> dict:
    try:
        job_id, index = verify_upload_token(token, _web_cfg.internal_api_secret)
    except UploadTokenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    _expire_pending_builds()
    with _jobs_lock:
        pending = _pending_builds.get(job_id)
        if pending is None:
            raise HTTPException(status_code=410, detail="This build is no longer waiting for uploads.")
        if not 0 <= index < len(pending.slots):
            raise HTTPException(status_code=403, detail="Upload token doesn't match this build.")
        slot = pending.slots[index]
        if slot.state != "waiting":
            detail = "is already uploading" if slot.state == "receiving" else "was already uploaded"
            raise HTTPException(status_code=409, detail=f"{slot.name} {detail}.")
        slot.state = "receiving"

    part = slot.path.with_name(slot.path.name + ".part")
    size = 0
    try:
        part.parent.mkdir(parents=True, exist_ok=True)
        with part.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > _web_cfg.max_upload_bytes:
                    raise HTTPException(status_code=413, detail=f"{slot.name} exceeds the upload size limit.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail=f"Empty upload for {slot.name}. Check the local path.")
        part.replace(slot.path)
    except BaseException:
        part.unlink(missing_ok=True)
        with _jobs_lock:
            slot.state = "waiting"  # retryable with the same URL
        raise

    with _jobs_lock:
        slot.state = "received"
        if _pending_builds.get(job_id) is not pending:
            raise HTTPException(status_code=410, detail="Upload window expired while this file was uploading.")
        received = sum(s.state == "received" for s in pending.slots)
        remaining = len(pending.slots) - received
        job = pending.task.job
        if remaining == 0:
            del _pending_builds[job_id]
            job.status, job.message = "queued", ""
        else:
            job.message = f"Waiting for uploads ({received} of {len(pending.slots)})"
        row = job.row()
    _db().upsert_job(row)
    if remaining == 0:
        _enqueue(pending.task)
    return {"file": slot.name, "bytes": size, "remaining": remaining, "status": row["status"]}


# RFC 8414 / RFC 9728 discovery lives at the *host root* with the resource
# path appended (`/.well-known/oauth-protected-resource/mcp/`,
# `/.well-known/oauth-authorization-server/mcp`) — that's what the 401's
# `resource_metadata` points clients at. Mounting FastMCP under `/mcp` only
# serves them under `/mcp/.well-known/...`, so a real client (Claude Code)
# 404'd on discovery and fell back to a root `/register`. `mcp_path="/"`
# because `mcp_app` is `http_app(path="/")`; the `/mcp` prefix is already in
# the provider's base_url. The Ingress routes these prefixes here too.
if mcp.auth is not None:
    app.router.routes.extend(mcp.auth.get_well_known_routes(mcp_path="/"))

app.mount("/mcp", mcp_app)
