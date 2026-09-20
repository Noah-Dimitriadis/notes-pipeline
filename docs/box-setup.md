# 9060 XT box setup

Operational runbook for the gaming box that hosts the M10–M16 stack
(§14 of `PLAN.md`). `PLAN.md` is the architecture/spec; this is the
step-by-step "what to actually do on the machine" checklist, kept separate
so the two don't get tangled.

Nothing in `deploy/` has been run on real hardware yet — this is the plan
for doing that, written before the box was ready.

---

## 1. One-time OS setup

- [ ] Ubuntu installed and fully updated (`apt update && apt full-upgrade`).
- [x] `unattended-upgrades` enabled — it's on this checklist now, not
      deferred to M16, since the box has no public port today but will
      still be reachable from goosenest02 and the tailnet.
- [x] Tailscale installed and joined to the tailnet:
      `curl -fsSL https://tailscale.com/install.sh | sh` then
      `tailscale up`.
  - [x] Record the box's Tailscale IPv4 (`tailscale ip -4`) — goes in
        `deploy/.env` as `TAILSCALE_IP`, and is also what goosenest02's
        Ingress needs to target (M11). Recorded: `100.119.131.69`.
  - [ ] Record the box's Tailscale MagicDNS hostname too
        (`tailscale status` — looks like `<name>.<tailnet>.ts.net`).
        `PLAN.md` prefers the hostname over the raw IP for M11's Ingress
        backend, since it survives re-registration.
- [x] **Fixed 2026-09-17:** SSH was reachable on `0.0.0.0:22`/`[::]:22`
      (sshd still listens on all interfaces — `ListenAddress` is
      unchanged) but is now locked to Tailscale at the firewall. `ufw`
      was inactive; brought it up with a default-deny-incoming policy
      plus explicit rules scoping port 22 to `tailscale0`:
      ```bash
      sudo ufw default deny incoming
      sudo ufw default allow outgoing
      sudo ufw allow in on tailscale0 to any port 22 proto tcp comment 'SSH via Tailscale only'
      sudo ufw deny 22/tcp comment 'block SSH on all other interfaces'
      sudo ufw enable
      ```
      `ufw status numbered` confirms both the v4 and v6 allow-on-tailscale0
      rules sit above their corresponding deny-22 rules (correct — ufw
      matches top to bottom, so tailnet SSH is allowed before the
      catch-all deny is reached). SSH from a tailnet-connected Mac
      confirmed working post-change. Firewall approach was used over
      `sshd_config`'s `ListenAddress` specifically so this doesn't break
      if the box's Tailscale IP (`100.119.131.69`) ever changes.
      **Still pending:** confirming SSH is actually refused from a
      LAN/WAN-only client (Tailscale off) — do this next and update this
      note once done.
- [x] Confirm nothing is listening on a public interface at all yet:
      `ss -tlnp` cross-checked against `ip a` — as of 2026-09-17, only
      `sshd` was on `0.0.0.0`/`[::]` (fixed above via ufw). Everything
      else was already scoped correctly: `tailscaled` binds to the
      Tailscale IP, both `systemd-resolved` listeners are loopback-only,
      and `docker0` was down (no compose stack running yet, so no
      container-published ports to check). Re-run this sweep once
      `docker compose up -d` happens in §3, since published container
      ports can bypass ufw's INPUT chain.
- [x] Docker Engine + Compose plugin installed
      (`docker compose version` should show v2+). Docker 29.8.1 / Compose
      v5.5.1.
- [x] AMD GPU: confirm the `amdgpu` kernel driver is loaded
      (`lsmod | grep amdgpu`) and `/dev/dri/` has render nodes
      (`ls -la /dev/dri`). Confirmed: `renderD128` + `card1` present.
  - [x] `getent group render` and `getent group video` — write these GIDs
        down, they go in `deploy/.env` as `RENDER_GID`/`VIDEO_GID`. The
        Dockerfile's build-arg defaults (104, 44) are common Debian/Ubuntu
        values but **not guaranteed to match this box** — don't skip this.
        Recorded: `RENDER_GID=991`, `VIDEO_GID=44` (matches `getent`).
  - [ ] Optional host-side sanity check before touching Docker at all:
        `sudo apt install vulkan-tools && vulkaninfo --summary` — confirms
        the GPU + Vulkan userspace stack works at the OS level, so if
        something's wrong later you know whether to debug the host or the
        container.

---

## 2. Repo + secrets on the box

- [x] Clone/pull this repo onto the box. On `main` @ `bb09629`.
- [x] `cp deploy/.env.example deploy/.env` and fill in the three values
      recorded in §1 (`TAILSCALE_IP`, `RENDER_GID`, `VIDEO_GID`).
      `deploy/.env` is gitignored — it never leaves this machine, and if
      the box is ever reimaged this file is gone and needs regenerating
      from scratch, not restored from git. Also has the M13 vars filled
      in, with `AUTH_ENABLED=false` set deliberately for the first smoke
      test (Google OAuth secrets not filled in yet — intentional).
- [x] The whisper model file needs to land in the `whisper-model` named
      volume before `api` will actually work (it's mounted read-only from
      that volume, not baked into the image). Get the model file onto the
      box first (scp/rsync from the Mac, or download fresh — same
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
      Confirmed present: `ggml-large-v3-turbo.bin`, 1.6GB, in
      `notes-pipeline_whisper-model`.
  - [ ] Keep a copy of the model file somewhere off this box too — it's
        1.6 GB and isn't (and shouldn't be) committed to git, so this
        volume is presently the only copy on this machine.

---

## 3. Build and bring up

- [x] `cd deploy && docker compose build` — the `api` image compiles
      whisper.cpp from source with Vulkan; this is the slow one, expect
      it to take a while the first time. Images present:
      `notes-pipeline-api:local`, `notes-pipeline-web:local`.
      **Note:** the first build here was stale — it dated from before M13's
      Dockerfile changes and still had `placeholder.py` baked in with no
      `notes_pipeline` package, so `api` was silently running the
      placeholder after `up -d` even though `healthz` returned `ok`. A
      forced rebuild (`docker compose build api`) fixed it — worth a
      sanity check (`docker inspect <container> --format
      '{{.Config.Cmd}}'`) after any future rebuild if something feels off,
      since a passing healthz doesn't guarantee the real app is running.
  - [ ] Before trusting this build long-term: pin `WHISPER_CPP_REF` in
        `deploy/api/Dockerfile` to an actual tag instead of `master`, and
        confirm `-DGGML_VULKAN=1` is still the right cmake flag for
        whatever tag gets pinned — flagged as unverified when this
        Dockerfile was written.
- [x] `docker compose up -d`
- [x] `docker compose ps` — all three services should be `running`, `api`
      should report `healthy` once its healthcheck passes. Confirmed
      2026-09-17: `api` healthy, `web`/`admin` up. (`api` also needed
      `KEY_MASTER_KEY`/`INTERNAL_API_SECRET` in `.env` — required
      unconditionally by `WebConfig` even with `AUTH_ENABLED=false` — and
      `web`/`admin` needed `AUTH_SECRET`; none of the three were in `.env`
      yet, generated and added on-box.)

---

## 4. Verify against M10's actual acceptance criteria

From the box itself:
- [x] `curl http://$(tailscale ip -4):8000/healthz` → `ok`. Re-verified
      2026-09-17 against the real `api` image (see §3 note — the first
      pass had been against a stale placeholder image without realizing).
- [x] `curl http://$(tailscale ip -4):8000/healthz/gpu` → 200, and the
      body shows `whisper-cli`'s `--help` output (confirms the binary and
      its Vulkan shared libs actually resolve — **not** a real
      GPU-accelerated transcription test, just that the binary runs).
- [x] `curl http://127.0.0.1:8090` → 307 (redirect — expected now that
      it's the real M14/M15 Next.js `admin` app, not the M10 static
      placeholder page this line originally described).

From another machine on the tailnet (e.g. your Mac, or goosenest02 once
it's set up):
- [x] The same `:8000/healthz` and `:3000` URLs, against the box's
      Tailscale IP, succeed (checked from Mac).
  - **Re-verify recommended:** this was checked before the §3 stale-image
    fix landed — worth a quick re-check now that the real `api` is
    running, since the earlier pass may have hit the placeholder.
- [ ] `:8090` (admin) **fails** — it's loopback-only, this is the check
      that matters most.

From a machine that is *not* on the tailnet (phone on cellular, etc.):
- [ ] Nothing on this box is reachable at all. This is really a check
      that the router/firewall has no port-forwarding rule pointed at
      this box — worth confirming directly on the router's admin page,
      not just inferring it from the compose file.

Real GPU transcription test (do this once a scratch wav is on the box —
e.g. copy `testlecture/lecture.wav` over):
- [x] `docker compose exec api whisper-cli -m /models/ggml-large-v3-turbo.bin -f /path/to/test.wav -l en -t 4 -mc 0` and confirm it actually runs on the GPU, not falling back to CPU (check timing against the Benchmark B numbers in `PLAN.md` §2 — GPU should be well under the ~6 min CPU time on the Mac).
      Confirmed 2026-09-17: `ggml_vulkan: Found 1 Vulkan devices` at
      startup (no CPU fallback). Total time (whisper's own
      `whisper_print_timings`, matches `time`'s wall clock): **126.7s
      (2:06.87)** vs. the 374.25s (6:14) CPU baseline on the Mac —
      roughly 1/3 the wall-clock time. Clean run, no repetition-loop
      artifacts, once run with `-mc 0` actually included (first attempt's
      shell command got line-split on paste and silently dropped `-l`/
      `-t`/`-mc 0`, which reproduced the exact repetition-loop failure
      mode `PLAN.md` §2 documents for missing `-mc 0` — worth being
      careful pasting multi-line commands into this box's shell).

---

## 5. Ongoing reminders (don't forget these later)

- This is a gaming PC. When it's off or rebooted into something that
  isn't running Docker, the service is down — that's expected, it's
  exactly what M11's fallback page on goosenest02 is for. No action
  needed, just don't be surprised by it.
- TLS and DNS are goosenest02's job (M11), not this box's — this box
  never needs a certificate of its own.
- M13 is done — `deploy/api/Dockerfile`'s `CMD` now runs the real
  `notes_pipeline.webapi:app` via uvicorn. `deploy/.env` needs the new
  `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`MCP_BASE_URL`/`ALLOWED_HOST`/
  `KEY_MASTER_KEY`/`INTERNAL_API_SECRET`/`ADMIN_EMAIL` values filled in
  (see `.env.example`) before `docker compose up` will serve real traffic.
- Once M14/M15 (the real Next.js app) exist, `deploy/web/Dockerfile`
  should be replaced outright, not built on top of.
- If the box is ever reimaged or Docker is reinstalled: `RENDER_GID` /
  `VIDEO_GID` may change (they're assigned by the OS, not fixed) —
  recheck `getent group render video` and update `deploy/.env` before
  assuming GPU passthrough still works.
- Periodically re-check that nothing new is listening on a public
  interface (`ss -tlnp` per §1) — easy to lose track of as more gets
  installed on this machine over time for gaming/other uses.
