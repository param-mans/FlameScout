// Vite plugin: serves the live house data routes (/api/house-viewer/*, see handler.mjs) inside `npm run dev` and
// `npm run preview`, so development needs no extra process. In the deploy, the same handler runs in the `house-data`
// container (standalone.mjs) and Caddy routes /api/house-viewer/* to it.
// Credentials come from the environment or the repo's .env (gitignored), loaded only here on the server.
import path from "node:path";
import { loadEnv } from "vite";
import { createHouseDataHandler } from "./handler.mjs";

export function houseData() {
  let handle = (req, res, next) => next();
  return {
    name: "flamescout-house-data",
    configResolved(config) {
      const env = loadEnv(config.mode, path.resolve(config.root, "..", ".."), ["VEXCEL_", "MATTERPORT_"]);
      handle = createHouseDataHandler({
        getEnv: (k) => process.env[k] || env[k],
        catalogFile: path.join(config.publicDir, "house-viewer", "samples", "index.json"),
      });
    },
    // middlewares added here run before Vite's own (including the /api proxy to :8000)
    configureServer(server) { server.middlewares.use((req, res, next) => handle(req, res, next)); },
    configurePreviewServer(server) { server.middlewares.use((req, res, next) => handle(req, res, next)); },
  };
}
