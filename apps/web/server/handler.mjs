// Live house data for the house viewer (framework-agnostic Node request handler). Used by the Vite plugin
// (house-data.mjs, `npm run dev` / `npm run preview`) and by the standalone server (standalone.mjs, deploy).
// Everything comes from the APIs at request time; nothing is written to disk (in-memory cache only, per process).
//
//   GET /api/house-viewer/sample/<id>                         -> { id, address, location, matterportModelId, listing,
//                                                                  quality, stairs, plan }   (plan from the Matterport API,
//                                                                  plus plan.stairs[] and opening.exterior: plan-extras.mjs)
//   GET /api/house-viewer/vexcel/<id>                         -> { views: { dir: { file, captureDate } }, ... }
//   GET /api/house-viewer/vexcel/<id>/oblique-<dir>.jpg       -> JPEG (Vexcel API)
//
// Credentials (server side only, never sent to the browser): VEXCEL_API_KEY; Matterport API token as
// MATTERPORT_API_KEY (token id) + MATTERPORT_API_SECRET (token secret), or MATTERPORT_API_KEY="<id>:<secret>".
// Matterport is read-only (queries only, see matterport-plan.mjs).
import fs from "node:fs";
import { generar, MPError } from "./matterport-plan.mjs";
import { evaluar } from "./house-quality.mjs";
import { obliques, VXError } from "./vexcel.mjs";
import { withPlanExtras } from "./plan-extras.mjs";

export const PREFIX = "/api/house-viewer/";
const TTL_MS = 6 * 3600 * 1000; // in-memory cache lifetime

/** getEnv(name) -> value; catalogFile = path of public/house-viewer/samples/index.json. Returns (req, res, next) => void. */
export function createHouseDataHandler({ getEnv, catalogFile }) {
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

  const secret = (k) => (getEnv(k) || "").trim();
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
    // plan from the API + extras computed once (stairs[] with stable ids, exterior flag on openings), see plan-extras.mjs
    const plan = await memo(`plan:${s.matterportModelId}`, () => generar(s.matterportModelId, matterportAuth()).then(withPlanExtras));
    const { quality, stairs } = evaluar(plan, { sqft: s.listing?.sqft ?? null, beds: s.listing?.beds ?? null });
    const { id, address, location, city, market, matterportModelId, listing } = s;
    return { id, address, location: { ...location, city, market }, matterportModelId, listing, quality, stairs, plan };
  }

  return async function handle(req, res, next) {
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
  };
}
