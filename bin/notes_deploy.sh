#!/bin/bash
# Rebuild and restart the treehouse Compose stack (web/api/admin).
#   mac       — run from the Mac: pulls and rebuilds on treehouse over SSH
#   treehouse — run on treehouse itself, against this checkout
# Environment can be passed as $1 or entered at the prompt.
set -euo pipefail

environment="${1:-}"
if [[ -z "$environment" ]]; then
  read -e -p "Environment (mac|treehouse): " environment
fi

# Same steps either way; only where they run differs.
rebuild='git pull --ff-only && cd deploy && docker compose build && docker compose up -d && docker compose ps'

case "$environment" in
  mac)
    # One-shot remote command: everything after `ssh treehouse` used to run
    # on the Mac once the interactive SSH session exited.
    ssh treehouse "cd ~/Documents/notes-pipeline && $rebuild"
    ;;
  treehouse)
    cd "$(dirname "$0")/.."
    eval "$rebuild"
    ;;
  *)
    echo "invalid environment: '$environment' (expected mac or treehouse)" >&2
    exit 1
    ;;
esac

# optional: add a health check and rollback for fully automated deployments;
# strictly user monitored for now
