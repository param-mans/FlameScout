// Vite plugin: live house data for the house viewer, served by the Vite server itself (`npm run dev` / `npm run preview`).
// Everything comes from the APIs at request time; nothing is written to disk (in-memory cache only, per server process).
//
//   GET /api/house-viewer/sample/<id>                         -> { id, address, location, matterportModelId, listing,
//                                                                  quality, stairs, plan }   (plan from the Matterport API)
//   GET /api/house-viewer/vexcel/<id>                         -> { views: { dir: { file, captureDate } }, ... }
//   GET /api/house-viewer/vexcel/<id>/oblique-<dir>.jpg       -> JPEG (Vexcel API)
//
// The house catalog (address, coordinates, Matterport model id, listing beds / sq ft) is public/house-viewer/samples/index.json.
// Credentials come from the environment or the repo's .env (gitignored), loaded only here on the server, never exposed to
// client code: VEXCEL_API_KEY; Matterport API token as MATTERPORT_API_KEY (token id) + MATTERPORT_API_SECRET (token secret),
// or MATTERPORT_API_KEY="<id>:<secret>". Matterport is read-only (queries only, see matterport-plan.mjs).
import fs from "node:fs";
import path from "node:path";
import { loadEnv } from "vite";
import { generar, MPError } from "./matterport-plan.mjs";
import { evaluar } from "./house-quality.mjs";
import { obliques, VXError } from "./vexcel.mjs";

const PREFIX = "/api/house-viewer/";
const TTL_MS = 6 * 3600 * 1000; // in-memory cache lifetime

export function houseData() {
  let env = {}, catalogFile = "";
  const cache = new Map(); // key -> { t, value: Promise }

  /** Cached by key for TTL_MS; concurrent requests share one fetch; failures are not cached. */
  function memo(key, fn) {
    const hit = cache.get(key);
    if (hit && Date.now() - hit.t < TTL_MS) return hit.value;
    const value = fn();
    cache.set(key, { t: Date.now(), value });
    value.catch(() => cache.delete(key));
    return value;
  }

  const secret = (k) => (process.env[k] || env[k] || "").trim();
  function matterportAuth() {
    const key = secret("MATTERPORT_API_KEY"), sec = secret("MATTERPORT_API_SECRET");
    const [id, pass] = key.includes(":") && !sec ? key.split(/:(.*)/s) : [key, sec];
    return id && pass ? `Basic ${Buffer.from(`${id}:${pass}`).toString("base64")}` : "";
  }

  const send = (res, status, body, type = "application/json") => {
    res.statusCode = status;
    res.setHeader("Content-Type", type);
    res.setHeader("Cache-Control", type === "image/jpeg" ? "private, max-age=3600" : "no-cache");
    res.end(type === "application/json" ? JSON.stringify(body) : body);
  };

  async function sampleDoc(s) {
    const plan = await memo(`plan:${s.matterportModelId}`, () => generar(s.matterportModelId, matterportAuth()));
    const { quality, stairs } = evaluar(plan, { sqft: s.listing?.sqft ?? null, beds: s.listing?.beds ?? null });
    const { id, address, location, city, market, matterportModelId, listing } = s;
    return { id, address, location: { ...location, city, market }, matterportModelId, listing, quality, stairs, plan };
  }

  async function handle(req, res, next) {
    const url = new URL(req.url ?? "/", "http://local");
    if (req.method !== "GET" || !url.pathname.startsWith(PREFIX)) return next();
    const [kind, id, file, ...extra] = url.pathname.slice(PREFIX.length).replace(/^\/+|\/+$/g, "").split("/");
    if (kind !== "sample" && kind !== "vexcel") return next();
    try {
      const s = JSON.parse(fs.readFileSync(catalogFile, "utf8")).find((x) => x.id === id);
      if (!s || extra.length || (kind === "sample" && file)) return send(res, 404, { error: "unknown sample" });
      if (kind === "sample") return send(res, 200, await sampleDoc(s));
      if (!s.location) return send(res, 404, { error: "no location for this sample" });
      const vx = await memo(`vexcel:${s.id}`, () => obliques(secret("VEXCEL_API_KEY"), s.location.lat, s.location.lon));
      if (!file) { const { source, copyright, convention, views } = vx.meta; return send(res, 200, { source, copyright, convention, views }); }
      const m = /^oblique-(north|east|south|west)\.jpg$/.exec(file);
      const img = m && vx.images[m[1]];
      return img ? send(res, 200, img, "image/jpeg") : send(res, 404, { error: "no image for this view" });
    } catch (e) {
      // messages never include credentials
      const known = e instanceof MPError || e instanceof VXError;
      return send(res, known ? e.status || 502 : 500, { error: known ? e.message : `unexpected error: ${e.name}` });
    }
  }

  return {
    name: "flamescout-house-data",
    configResolved(config) {
      env = loadEnv(config.mode, path.resolve(config.root, "..", ".."), ["VEXCEL_", "MATTERPORT_"]);
      catalogFile = path.join(config.publicDir, "house-viewer", "samples", "index.json");
    },
    // middlewares added here run before Vite's own (including the /api proxy to :8000)
    configureServer(server) { server.middlewares.use(handle); },
    configurePreviewServer(server) { server.middlewares.use(handle); },
  };
}
