# Local dev (no GPU, no Tailscale)

How to run the hosted `api`/`web` stack (the same one treehouse runs) on a
workstation with no AMD/Vulkan GPU — e.g. this repo's own Mac — so the
whole upload → transcribe → synthesize → notes path can be verified
end-to-end without needing treehouse itself.

## Why this needs its own compose file

`deploy/docker-compose.yml` is treehouse-specific: it passes `/dev/dri` and
`/dev/kfd` through for AMD Vulkan GPU access, and binds every port to
`${TAILSCALE_IP}`. Neither exists on a workstation with no GPU passthrough
and no Tailscale — `docker compose up` against that file directly would
either fail to start (no such device) or bind nowhere useful (empty
`TAILSCALE_IP`).

`deploy/docker-compose.local.yml` is the same `api`/`web` images, same
build context, with only those two things different: no `devices:` block,
and ports bound to `127.0.0.1` instead.

## Why transcription still works with no GPU

whisper.cpp's `-ng`/`--no-gpu` flag forces a CPU-only run even in a binary
compiled with Vulkan support. `notes_pipeline/stages/transcribe.py`'s
`_gpu_available()` probes for a real Linux GPU render node
(`/dev/dri/render*`) and `WhisperTranscriber` adds `-ng` automatically when
it finds none — controlled by `Config.whisper_device`
(env var `WHISPER_DEVICE`, default `auto`):

| `WHISPER_DEVICE` | Behavior |
|---|---|
| `auto` (default) | Probe for `/dev/dri/render*` (Linux) or assume Metal (macOS, native — not this Docker path); use CPU (`-ng`) only if neither is found. |
| `gpu` | Never add `-ng` — fails loudly if no GPU backend is actually available, instead of silently degrading to a slow CPU run. Useful on treehouse itself, to catch a broken Vulkan setup immediately rather than discover it from a suspiciously long transcribe stage. |
| `cpu` | Always add `-ng`. |

`docker-compose.local.yml` sets `WHISPER_DEVICE=auto` explicitly (matching
the default, just for clarity) — since this box has no `/dev/dri` passed
through at all, it always lands on CPU. Nothing else about the pipeline
changes; transcription is just slower than treehouse's GPU-accelerated
path (§2 of `PLAN.md`: whisper `large-v3-turbo` is still usable on CPU, just
not the ~6-minutes-per-lecture figure benchmarked there).

**Not needed for the Mac's own `notes` CLI.** Running `notes build`
directly (not through Docker) on this Mac already gets real GPU
acceleration for free — the Homebrew `whisper-cli` here auto-initializes
Metal and only needs `-ng` if you explicitly want to disable it
(`WHISPER_DEVICE=cpu`). The auto-detection above only forces CPU inside the
Linux container, where there's no Metal to fall back to.

## Running it

```bash
cp deploy/.env.local.example deploy/.env.local
```

Fill in the two generated values `.env.local.example` calls out
(`KEY_MASTER_KEY`, `AUTH_SECRET`) — everything else in that file is a
placeholder that's fine to leave as-is for local testing.

```bash
docker compose -f deploy/docker-compose.local.yml up --build
```

The whisper.cpp build stage compiles from source regardless of target
device (same Dockerfile as treehouse) — expect the first build to take a
while.

Once it's up:

- `web` at http://localhost:3000 — signs you straight in as
  `dev-local@notes-pipeline.local` (`AUTH_ENABLED=false`, no real Google
  OAuth needed).
- `api` health check: `curl http://localhost:8000/healthz` → `ok`.
- Add a real Anthropic API key via Settings before uploading anything —
  synthesis still calls the real Claude API even in local/no-GPU mode; only
  transcription's device selection changes.

**Not yet verified against real Docker Desktop hardware** (this doc and
`docker-compose.local.yml` were written and `docker compose config`-checked
on this machine, but the actual multi-stage `whisper.cpp` build wasn't run
to completion here — Docker Desktop's daemon wasn't running at the time).
Treat the first real `--build` as the verification step, same spirit as
`docs/treehouse-setup.md`'s own "written and reviewed on a Mac, not yet run
on real hardware" caveat for the treehouse compose file.

## Multi-part audio (CLI)

`notes build --audio` and `notes append --audio` both now accept multiple
files, repeatable, in chronological order — for a lecture recorded in
several parts (recorder paused/restarted mid-lecture):

```bash
notes build --audio part1.m4a --audio part2.m4a --out Week1-lecture-notes.md
```

They're concatenated (via `stages/audio.prepare_many`, an ffmpeg
`concat`+`loudnorm` filter graph) into one continuous recording before
transcription — same downstream pipeline as a single file. The hosted
web upload form is still single-file only (a separate, deliberate
limitation — see the `single_audio_file_limit` note); this is CLI-only for
now.

## Job history

`GET /api/jobs` (proxied by `web` at the same path) returns the calling
user's build-job history, newest first, persisted in the `jobs` SQLite
table — survives an `api` restart, unlike the previous in-memory-only job
tracking. The Lectures page renders it below the lecture table. A job
still `queued`/`running` when `api` starts up (crash, redeploy) is marked
`error` ("Interrupted by a server restart.") rather than left showing as
in-progress forever.
