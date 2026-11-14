# Deploying FlameScout on Vultr

One Vultr Cloud Compute instance runs three containers:

- `api`: FastAPI and the agents, on port 8000 inside the Docker network only.
- `house-data`: Node service on port 8080, inside the Docker network only (`apps/web/server`, no npm
  dependencies). It serves `/api/house-viewer/*` for the house viewer: floor plans, quality and stairs generated live
  from the Matterport API, plus Vexcel oblique views. It reads `MATTERPORT_API_KEY`, `MATTERPORT_API_SECRET` and
  `VEXCEL_API_KEY` from `.env` at runtime and keeps results in memory only.
- `web`: Caddy. It serves the built Vite UI and forwards `/api/house-viewer/*` to `house-data`, and `/api`, `/ws`,
  `/health` and `/docs` to `api`, all from one address. Because the UI and API share an address, there's no CORS setup, and the WebSocket uses `wss://` whenever the site is on HTTPS.

The databases stay managed: Postgres on Tiger Data (`TIGER_DATABASE_URL`) and the incident workspace on MongoDB Atlas (`MONGODB_URI`). Secrets and the licensed CoStar index are never built into an image. They're copied to the server and read at runtime.

## 1. Create the instance (Vultr console)

1. **Deploy, then Cloud Compute (Shared CPU)**. Choose the region closest to the demo (for example New York or New Jersey).
2. **Image:** Ubuntu 24.04 LTS x64.
3. **Plan:** 2 vCPU / 4 GB RAM or larger. The Vite build and the Python dependencies need the memory. 1 GB plans run out of memory during the build.
4. **SSH key:** add your public key (`cat ~/.ssh/id_ed25519.pub`).
5. Deploy, then copy the instance's **public IPv4**.

Optional: point a domain's A record at the IP. Without a domain, use `<ip-with-dashes>.sslip.io`, for example `45-77-1-2.sslip.io` for `45.77.1.2`. It resolves to your IP with no setup, and Caddy can get a real certificate for it.

If MongoDB Atlas restricts network access, add the instance IP under **Network Access**.

## 2. Bootstrap (once)

Run this from the repo root on your laptop:

```bash
export VULTR_IP=45.77.1.2                      # your instance IP
ssh root@$VULTR_IP "REPO_URL=$(git remote get-url origin) bash -s" < deploy/vultr-bootstrap.sh
```

This installs Docker, opens ports 22, 80 and 443 in `ufw`, clones the repo to `/opt/flamescout` and creates the `workspace/` and `data/` folders. The repo is public, so the HTTPS clone needs no credentials. If `origin` is an SSH URL, pass the `https://github.com/...` URL instead.

## 3. Configure

Add the site address to your local `.env`:

```bash
SITE_ADDRESS=45-77-1-2.sslip.io   # or your domain; ":80" = plain HTTP, no certificate
```

Check these values in `.env` too:

- `COSTAR_INDEX=data/costar/properties.jsonl`: a relative path. It resolves inside the container (`/app`), where the index is mounted read-only.
- `CORS_ORIGINS`: not needed in production because the UI and API share an address. Leave it as is.
- `TIGER_DATABASE_URL`, `MONGODB_URI`, the Gemini keys and `LANGSMITH_*`: same as local.

## 4. Deploy

The first deploy copies `.env` and the CoStar index:

```bash
VULTR_HOST=root@$VULTR_IP deploy/deploy.sh --sync
```

Later deploys: commit and push to `main`, then run:

```bash
VULTR_HOST=root@$VULTR_IP deploy/deploy.sh
```

The script runs `git pull`, `docker compose up -d --build` and `docker compose ps` on the server, then prints the API's `/health` response. Use `--sync` again whenever `.env` or the CoStar index changes.

`.env` and `data/costar/` live only on the server. A `git pull` never touches them.

## 5. Check it works

- `https://<SITE_ADDRESS>/`: the UI.
- `https://<SITE_ADDRESS>/health`: the API health check.
- `https://<SITE_ADDRESS>/docs`: the API docs.
- WebSocket: `wss://<SITE_ADDRESS>/ws/incidents/<id>`.

The first HTTPS request can take a few seconds while Caddy gets the certificate. Certificates are kept in the `caddy_data` volume, so rebuilds don't request new ones. That matters because Let's Encrypt has rate limits.

Note: the UI currently renders fixture data. It doesn't call the API yet (see the TODO in `apps/web/src/components/Inspector.tsx`). When it does, use relative URLs (`/api/...`, `/ws/...`) so the same build works locally through the Vite proxy and in production through Caddy.

## Operating

On the server, from `/opt/flamescout`:

```bash
docker compose logs -f api        # API and agent logs
docker compose logs -f web        # Caddy (certificates, proxy errors)
docker compose restart api        # after editing .env on the server
docker compose ps                 # status and health
docker system prune -f            # reclaim disk after many rebuilds
```

Run the one-off scripts inside the API container:

```bash
docker compose exec api python scripts/prefetch.py fake_wilshire_tower
docker compose exec api python scripts/init_tiger.py
```

## Local test of the same setup

```bash
docker compose up --build          # SITE_ADDRESS unset -> plain HTTP on :80
open http://localhost/             # UI
curl http://localhost/health       # API through Caddy
```

## Troubleshooting

- **Certificate errors:** DNS must point at the instance, and ports 80 and 443 must be open in `ufw` and in any Vultr firewall group. Check with `docker compose logs web`.
- **`api` keeps restarting:** run `docker compose logs api`. Usually `.env` is missing (rerun with `--sync`) or a database URL is wrong. Tiger and MongoDB writes are best-effort, so a bad URL logs errors but shouldn't stop the API from starting.
- **Out of memory during the build:** resize to 4 GB, or add swap: `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`.
