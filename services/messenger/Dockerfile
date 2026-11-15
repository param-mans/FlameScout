# FlameScout messenger: iMessage through Photon Stable (spectrum-ts) for the API. Build context is the repo root
# (docker-compose.yml). Credentials come from the env_file at runtime and are never built into the image.
FROM node:22-alpine
WORKDIR /app
COPY services/messenger/package.json services/messenger/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY services/messenger/server.mjs ./
ENV MESSENGER_PORT=8090 MESSENGER_HOST=0.0.0.0 NODE_ENV=production
EXPOSE 8090
USER node
CMD ["node", "server.mjs"]
