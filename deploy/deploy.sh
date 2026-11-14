#!/usr/bin/env bash
# Deploy the current main to the Vultr host. Run from the repo root on your laptop:
#   VULTR_HOST=root@<ip> deploy/deploy.sh            # pull + rebuild
#   VULTR_HOST=root@<ip> deploy/deploy.sh --sync     # also copy .env and the CoStar index first
set -euo pipefail

: "${VULTR_HOST:?set VULTR_HOST=root@<server-ip>}"
APP_DIR=/opt/flamescout

if [ "${1:-}" = "--sync" ]; then
  scp .env "$VULTR_HOST:$APP_DIR/.env"
  if [ -f data/costar/properties.jsonl ]; then
    scp data/costar/properties.jsonl "$VULTR_HOST:$APP_DIR/data/costar/properties.jsonl"
  fi
fi

ssh "$VULTR_HOST" "cd $APP_DIR && git pull --ff-only && docker compose up -d --build && docker compose ps"
# Check the API from inside its container (Caddy only answers for SITE_ADDRESS, not 127.0.0.1).
ssh "$VULTR_HOST" "cd $APP_DIR && sleep 5 && docker compose exec -T api python -c \"import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())\""
