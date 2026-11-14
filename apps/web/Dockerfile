# FlameScout web: build the Vite app, serve it from Caddy (which also proxies /api and /ws to the API
# and gets HTTPS certificates). Build context is the repo root, see docker-compose.yml.
FROM node:22-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv
