# M14/M15 — real Next.js web/admin image, replacing M10's placeholder nginx
# image. One image serves both the `web` and `admin` compose services;
# APP_MODE (env var, see web/.env.example) selects which route surface is
# reachable at runtime (web/src/middleware.ts) — never a build-time choice,
# so both services can share this one image per M15's constraint.
#
# IMPORTANT — build context mismatch found while building this (flagged per
# instructions rather than silently worked around): docker-compose.yml's
# `web`/`admin` services currently set `build: {context: web, dockerfile:
# Dockerfile}`, resolved relative to docker-compose.yml's own directory
# (deploy/) — i.e. build context `deploy/web/`. That matched the M10
# placeholder (Dockerfile + placeholder.html living right next to each
# other), but the real Next.js app lives at the repo root's `web/` per
# PLAN.md §14.4's own layout table and this module's instructions — this
# Dockerfile is written assuming that repo-root context (it COPYs
# `web/package.json`, `web/src`, etc., mirroring exactly how
# deploy/api/Dockerfile COPYs `notes_pipeline/` from a `context: ..`).
# docker-compose.yml's web/admin `build:` blocks need
# `context: ..` + `dockerfile: deploy/web/Dockerfile` (same shape as the
# `api` service already uses) before this file will actually build — left
# for the user to apply, since editing docker-compose.yml is out of scope
# here. deploy/web/placeholder.html is no longer referenced and can be
# deleted once that compose edit lands.

# ---- stage 1: install deps --------------------------------------------------
FROM node:22-bookworm-slim AS deps
WORKDIR /app
# better-sqlite3 is a native addon; prebuild-install covers most platforms
# but these fall back to compiling from source if no prebuilt binary
# matches this image's Node ABI/arch — without these, that fallback would
# fail silently on an unmatched platform.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 make g++ \
    && rm -rf /var/lib/apt/lists/*
COPY web/package.json web/package-lock.json ./
RUN npm ci

# ---- stage 2: build ----------------------------------------------------------
FROM node:22-bookworm-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY web/ .
RUN npm run build

# ---- stage 3: runtime ---------------------------------------------------------
FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# node:22's base image already ships a `node` user/group at uid/gid 1000
# (matching the api image's --uid 1000) — rename it rather than colliding
# with a fresh `useradd`.
RUN usermod -l notes -d /home/notes -m node && groupmod -n notes node

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
# Next's standalone output file-tracing has open bugs around native addons
# specifically (better-sqlite3's compiled binary) on recent Next/Turbopack
# versions — copying it in directly is cheap insurance against a build that
# traces clean but can't actually open a database at runtime.
COPY --from=deps /app/node_modules/better-sqlite3 ./node_modules/better-sqlite3

# Named volumes mounted here (docker-compose.yml) start out root-owned if
# the mount point doesn't already exist in the image — pre-creating them
# with the right ownership is what lets a non-root container actually
# write into them on first bring-up.
RUN mkdir -p /data/auth-db /data/notes-db && chown -R notes:notes /app /data

USER notes
EXPOSE 3000
CMD ["node", "server.js"]
