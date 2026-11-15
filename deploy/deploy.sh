#!/usr/bin/env bash
# Deploy the current main to the Vultr host. Run from the repo root on your laptop:
#   VULTR_HOST=root@<ip> deploy/deploy.sh            # pull + copy data/raw + rebuild
#   VULTR_HOST=root@<ip> deploy/deploy.sh --sync     # also copy .env and the CoStar index first
set -euo pipefail

: "${VULTR_HOST:?set VULTR_HOST=root@<server-ip>}"
APP_DIR=/opt/flamescout

if [ "${1:-}" = "--sync" ]; then
  # Keep the server's SITE_ADDRESS (Caddy's HTTPS name) when the local .env has none: without it the site
  # falls back to plain HTTP on :80.
  site=$(ssh "$VULTR_HOST" "grep '^SITE_ADDRESS=' $APP_DIR/.env 2>/dev/null || true")
  scp .env "$VULTR_HOST:$APP_DIR/.env"
  if [ -n "$site" ] && ! grep -q '^SITE_ADDRESS=' .env; then
    ssh "$VULTR_HOST" "printf '\n%s\n' '$site' >> $APP_DIR/.env"  # own line even if .env lacks a final newline
  fi
  if [ -f data/costar/properties.jsonl ]; then
    scp data/costar/properties.jsonl "$VULTR_HOST:$APP_DIR/data/costar/properties.jsonl"
  fi
fi

ssh "$VULTR_HOST" "cd $APP_DIR && git pull --ff-only"
# data/raw is sponsor data and not in git; docker-compose.yml mounts it read-only into api and house-data.
# Copied after the pull: the pull that untracked it deletes the server's tracked copy.
if [ -d data/raw ]; then
  ssh "$VULTR_HOST" "mkdir -p $APP_DIR/data"
  scp -rq data/raw "$VULTR_HOST:$APP_DIR/data/"
fi
ssh "$VULTR_HOST" "cd $APP_DIR && docker compose up -d --build && docker compose ps"
# Check the API from inside its container (Caddy only answers for SITE_ADDRESS, not 127.0.0.1).
ssh "$VULTR_HOST" "cd $APP_DIR && sleep 5 && docker compose exec -T api python -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())\""
