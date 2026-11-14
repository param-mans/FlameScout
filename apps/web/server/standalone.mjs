// Standalone server for the live house data routes (/api/house-viewer/*), used by the `house-data` container in the
// deploy (docker-compose.yml; Caddy routes /api/house-viewer/* here). Node built-ins only, no dependencies.
//   node apps/web/server/standalone.mjs        # listens on HOUSE_DATA_PORT (default 8080)
// Credentials come from the environment (docker compose env_file: .env): VEXCEL_API_KEY, MATTERPORT_API_KEY,
// MATTERPORT_API_SECRET. They are never logged or returned.
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHouseDataHandler } from "./handler.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.HOUSE_DATA_PORT || 8080);
const handle = createHouseDataHandler({
  getEnv: (k) => process.env[k],
  catalogFile: process.env.HOUSE_CATALOG || path.join(here, "..", "public", "house-viewer", "samples", "index.json"),
  storedDir: process.env.HOUSE_DATA_RAW || path.join(here, "..", "data", "raw"),
  trustProxy: process.env.HOUSE_DATA_TRUST_PROXY === "1", // behind Caddy: client IP = last X-Forwarded-For hop
});

http.createServer((req, res) => {
  const t0 = Date.now();
  res.on("finish", () => console.log(`${req.method} ${req.url?.split("?")[0]} ${res.statusCode} ${Date.now() - t0}ms`));
  if (req.url === "/health") { res.setHeader("Content-Type", "application/json"); return res.end('{"ok":true}'); }
  handle(req, res, () => { res.statusCode = 404; res.setHeader("Content-Type", "application/json"); res.end('{"error":"not found"}'); });
}).listen(port, () => {
  const has = (k) => (process.env[k] ? "set" : "MISSING");
  const mp = (process.env.MATTERPORT_API_KEY || process.env.MP_ID) && (process.env.MATTERPORT_API_SECRET || process.env.MP_SECRET || String(process.env.MATTERPORT_API_KEY).includes(":"));
  console.log(`house-data on :${port} (VEXCEL_API_KEY ${has("VEXCEL_API_KEY")}, Matterport token ${mp ? "set" : "MISSING: stored plans only"})`);
});
