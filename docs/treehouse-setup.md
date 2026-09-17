# treehouse setup

Operational runbook for treehouse, the gaming box that hosts the M10–M16
stack (§14 of `PLAN.md`). `PLAN.md` is the architecture/spec; this is the
step-by-step "what to actually do on the machine" checklist, kept separate
so the two don't get tangled.

Nothing in `deploy/` has been run on real hardware yet — this is the plan
for doing that, written before treehouse was ready.

---

## 1. One-time OS setup

- [ ] Ubuntu installed and fully updated (`apt update && apt full-upgrade`).
- [ ] `unattended-upgrades` enabled — it's on this checklist now, not
      deferred to M16, since treehouse has no public port today but will
      still be reachable from goosenest02 and the tailnet.
- [ ] Tailscale installed and joined to the tailnet:
      `curl -fsSL https://tailscale.com/install.sh | sh` then
      `tailscale up`.
  - [ ] Record treehouse's Tailscale IPv4 (`tailscale ip -4`) — goes in
        `deploy/.env` as `TAILSCALE_IP`, and is also what goosenest02's
        Ingress needs to target (M11).
  - [ ] Record treehouse's Tailscale MagicDNS hostname too
        (`tailscale status` — looks like `<name>.<tailnet>.ts.net`).
        `PLAN.md` prefers the hostname over the raw IP for M11's Ingress
        backend, since it survives re-registration.
- [ ] Confirm SSH is reachable **only** over Tailscale, not the LAN/WAN —
      check `sshd_config`'s `ListenAddress` and/or the host firewall
      (`ufw status` or equivalent). This matters more now than it did
      before this project: treehouse is about to have friends' Anthropic
      keys flowing through it.
- [ ] Confirm nothing is listening on a public interface at all yet:
      `ss -tlnp` and cross-check against `ip a` — every listener should be
      bound to `127.0.0.1` or the `tailscale0` interface, nothing on
      `0.0.0.0` or the LAN interface.
- [ ] Docker Engine + Compose plugin installed
      (`docker compose version` should show v2+).
- [ ] AMD GPU: confirm the `amdgpu` kernel driver is loaded
      (`lsmod | grep amdgpu`) and `/dev/dri/` has render nodes
      (`ls -la /dev/dri`).
  - [ ] `getent group render` and `getent group video` — write these GIDs
        down, they go in `deploy/.env` as `RENDER_GID`/`VIDEO_GID`. The
        Dockerfile's build-arg defaults (104, 44) are common Debian/Ubuntu
        values but **not guaranteed to match treehouse** — don't skip this.
  - [ ] Optional host-side sanity check before touching Docker at all:
        `sudo apt install vulkan-tools && vulkaninfo --summary` — confirms
        the GPU + Vulkan userspace stack works at the OS level, so if
        something's wrong later you know whether to debug the host or the
        container.

---

## 2. Repo + secrets on treehouse

- [ ] Clone/pull this repo onto treehouse.
- [ ] `cp deploy/.env.example deploy/.env` and fill in the three values
      recorded in §1 (`TAILSCALE_IP`, `RENDER_GID`, `VIDEO_GID`).
      `deploy/.env` is gitignored — it never leaves treehouse, and if
      treehouse is ever reimaged this file is gone and needs regenerating
      from scratch, not restored from git.
- [ ] The whisper model file needs to land in the `whisper-model` named
      volume before `api` will actually work (it's mounted read-only from
      that volume, not baked into the image). Get the model file onto treehouse
      first (scp/rsync from the Mac, or download fresh — same
      `ggml-large-v3-turbo.bin` referenced in the main `PLAN.md`
      prerequisites §12), then copy it into the volume with a throwaway
      container:
      ```bash
      docker volume create notes-pipeline_whisper-model
      docker run --rm \
        -v notes-pipeline_whisper-model:/models \
        -v /path/to/downloaded/models:/src:ro \
        alpine cp /src/ggml-large-v3-turbo.bin /models/
      ```
      Check the actual volume name with `docker volume ls` if the compose
      project name ever changes — don't assume the prefix.
  - [ ] Keep a copy of the model file somewhere off treehouse too — it's
        1.6 GB and isn't (and shouldn't be) committed to git, so this
        volume is presently the only copy on treehouse.

---

## 3. Build and bring up

- [ ] `cd deploy && docker compose build` — the `api` image compiles
      whisper.cpp from source with Vulkan; this is the slow one, expect
      it to take a while the first time.
  - [ ] Before trusting this build long-term: pin `WHISPER_CPP_REF` in
        `deploy/api/Dockerfile` to an actual tag instead of `master`, and
        confirm `-DGGML_VULKAN=1` is still the right cmake flag for
        whatever tag gets pinned — flagged as unverified when this
        Dockerfile was written.
- [ ] `docker compose up -d`
- [ ] `docker compose ps` — all three services should be `running`, `api`
      should report `healthy` once its healthcheck passes.

---

## 4. Verify against M10's actual acceptance criteria

From treehouse itself:
- [ ] `curl http://$(tailscale ip -4):8000/healthz` → `ok`
- [ ] `curl http://$(tailscale ip -4):8000/healthz/gpu` → 200, and the
      body shows `whisper-cli`'s `--help` output (confirms the binary and
      its Vulkan shared libs actually resolve — **not** a real
      GPU-accelerated transcription test, just that the binary runs).
- [ ] `curl http://127.0.0.1:8090` → the placeholder admin page.

From another machine on the tailnet (e.g. your Mac, or goosenest02 once
it's set up):
- [ ] The same `:8000/healthz` and `:3000` URLs, against treehouse's
      Tailscale IP, succeed.
- [ ] `:8090` (admin) **fails** — it's loopback-only, this is the check
      that matters most.

From a machine that is *not* on the tailnet (phone on cellular, etc.):
- [ ] Nothing on treehouse is reachable at all. This is really a check
      that the router/firewall has no port-forwarding rule pointed at
      treehouse — worth confirming directly on the router's admin page,
      not just inferring it from the compose file.

Real GPU transcription test (do this once a scratch wav is on treehouse —
e.g. copy `testlecture/lecture.wav` over):
- [ ] `docker compose exec api whisper-cli -m /models/ggml-large-v3-turbo.bin -f /path/to/test.wav -l en -t 4 -mc 0` and confirm it actually runs on the GPU, not falling back to CPU (check timing against the Benchmark B numbers in `PLAN.md` §2 — GPU should be well under the ~6 min CPU time on the Mac).

---

## 5. Ongoing reminders (don't forget these later)

- This is a gaming PC. When it's off or rebooted into something that
  isn't running Docker, the service is down — that's expected, it's
  exactly what M11's fallback page on goosenest02 is for. No action
  needed, just don't be surprised by it.
- TLS and DNS are goosenest02's job (M11), not treehouse's — treehouse
  never needs a certificate of its own.
- M13 is done — `deploy/api/Dockerfile`'s `CMD` now runs the real
  `notes_pipeline.webapi:app` via uvicorn. `deploy/.env` needs the new
  `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`MCP_BASE_URL`/`ALLOWED_HOST`/
  `KEY_MASTER_KEY`/`INTERNAL_API_SECRET`/`ADMIN_EMAIL` values filled in
  (see `.env.example`) before `docker compose up` will serve real traffic.
- Once M14/M15 (the real Next.js app) exist, `deploy/web/Dockerfile`
  should be replaced outright, not built on top of.
- If treehouse is ever reimaged or Docker is reinstalled: `RENDER_GID` /
  `VIDEO_GID` may change (they're assigned by the OS, not fixed) —
  recheck `getent group render video` and update `deploy/.env` before
  assuming GPU passthrough still works.
- Periodically re-check that nothing new is listening on a public
  interface (`ss -tlnp` per §1) — easy to lose track of as more gets
  installed on treehouse over time for gaming/other uses.
