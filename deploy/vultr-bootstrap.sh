#!/usr/bin/env bash
# One-time setup for a fresh Vultr Cloud Compute instance (Ubuntu 24.04), run as root:
#   ssh root@<ip> "REPO_URL=$(git remote get-url origin) bash -s" < deploy/vultr-bootstrap.sh
# Installs Docker, opens 22/80/443, clones the repo to /opt/flamescout.
set -euo pipefail

REPO_URL="${REPO_URL:-}"
APP_DIR=/opt/flamescout

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

if command -v ufw >/dev/null; then
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
  ufw --force enable
fi

if [ ! -d "$APP_DIR/.git" ]; then
  if [ -z "$REPO_URL" ]; then
    echo "Set REPO_URL, e.g. REPO_URL=https://github.com/<org>/<repo>.git" >&2
    exit 1
  fi
  git clone "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/workspace" "$APP_DIR/data/cache" "$APP_DIR/data/costar"

echo "Done. Next, from your laptop: copy .env and data/costar/properties.jsonl, then run deploy/deploy.sh."
