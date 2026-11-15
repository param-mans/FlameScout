# FlameScout house data: live Matterport floor plans, quality, stairs and Vexcel imagery for the house viewer
# (/api/house-viewer/*). Node built-ins only, so there is no npm install. Build context is the repo root, see
# docker-compose.yml. Credentials come from the env_file at runtime and are never built into the image.
FROM node:22-alpine
WORKDIR /app
# package.json only for "type": "module" (escaleras.js / evac.js are ES modules shared with the viewer)
COPY apps/web/package.json ./package.json
COPY apps/web/server ./server
COPY apps/web/public/house-viewer/escaleras.js apps/web/public/house-viewer/evac.js ./public/house-viewer/
COPY apps/web/public/house-viewer/incident/plan-index.js ./public/house-viewer/incident/plan-index.js
COPY apps/web/public/house-viewer/samples/index.json ./public/house-viewer/samples/index.json
# stored raw Matterport responses of the star buildings (served when no Matterport credentials are configured) are
# mounted read-only at /app/data/raw by docker-compose.yml: sponsor data never goes into the image.
ENV HOUSE_DATA_PORT=8080 NODE_ENV=production
EXPOSE 8080
USER node
CMD ["node", "server/standalone.mjs"]
