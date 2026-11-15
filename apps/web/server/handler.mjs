// Live house data for the house viewer (framework-agnostic Node request handler). Used by the Vite plugin
// (house-data.mjs, `npm run dev` / `npm run preview`) and by the standalone server (standalone.mjs, deploy).
// Everything comes from the APIs at request time; nothing is written to disk (in-memory cache only, per process).
//
//   GET /api/house-viewer/sample/<id>                         -> { id, address, location, matterportModelId, listing,
//                                                                  quality, stairs, plan }   (plan from the Matterport API,
//                                                                  plus plan.stairs[] and opening.exterior: plan-extras.mjs)
//   GET /api/house-viewer/plan/<id>                           -> { id, model_id, plan_source, plan, plan_index }   (the same
//                                                                  plan as /sample, plus its plan index built with the viewer's
//                                                                  module; the backend caches both for on-demand houses)
//   GET /api/house-viewer/vexcel/<id>                         -> { views: { dir: { file, captureDate } }, ... }
//   GET /api/house-viewer/vexcel/<id>/oblique-<dir>.jpg       -> JPEG (Vexcel API)
//
// Credentials (server side only, never sent to the browser): VEXCEL_API_KEY; Matterport API token as
// MATTERPORT_API_KEY (token id) + MATTERPORT_API_SECRET (token secret), or MATTERPORT_API_KEY="<id>:<secret>".
// Matterport is read-only (queries only, see matterport-plan.mjs).
import fs from "node:fs";
import path from "node:path";
import { generar, planDesdeRespuesta, fechasModelo, MPError } from "./matterport-plan.mjs";
import { evaluar } from "./house-quality.mjs";
import { obliques, VXError } from "./vexcel.mjs";
import { withPlanExtras } from "./plan-extras.mjs";
import { createCatalogLookup } from "./house-catalog.mjs";
import { buildPlanIndex } from "../public/house-viewer/incident/plan-index.js";

export const PREFIX = "/api/house-viewer/";
const TTL_MS = 6 * 3600 * 1000; // in-memory cache lifetime
const RATE = { windowMs: 60_000, max: 120 }; // requests per IP per minute (a house load is ~6 requests)
const ID_RE = /^[a-z0-9-]{1,64}$/;

/** Only catalog ids are served (public/house-viewer/samples/index.json, then the house catalog data/raw/catalog.csv,
 *  houseCatalog option, default <storedDir>/catalog.csv; see house-catalog.mjs): never an arbitrary model id or location, so this is
 *  not an open proxy. Responses never carry keys, auth headers or signed URLs. Per-IP rate limit and in-memory cache.
 *  Without Matterport credentials the plan comes from the stored raw API response, data/raw/<id>/matterport/raw_floors.json
 *  (star buildings), through the same pipeline.
 *  getEnv(name) -> value; catalogFile = samples/index.json; storedDir = data/raw; trustProxy = take the client IP from the
 *  last X-Forwarded-For hop (only behind our own reverse proxy). Returns (req, res, next) => void. */
export function createHouseDataHandler({ getEnv, catalogFile, storedDir = null, trustProxy = false, houseCatalog = undefined }) {
  const catalogLookup = createCatalogLookup(houseCatalog === undefined ? (storedDir ? path.join(storedDir, "catalog.csv") : null) : houseCatalog);
  /** samples/index.json first, then the house catalog; null for any other id */
  const findSample = (id) => JSON.parse(fs.readFileSync(catalogFile, "utf8")).find((x) => x.id === id) || catalogLookup(id);
  const cache = new Map(); // key -> { t, value: Promise }
  const hits = new Map(); // ip -> { t0, n }

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
    const key = secret("MATTERPORT_API_KEY") || secret("MP_ID"), sec = secret("MATTERPORT_API_SECRET") || secret("MP_SECRET");
    const [id, pass] = key.includes(":") && !sec ? key.split(/:(.*)/s) : [key, sec];
    return id && pass ? `Basic ${Buffer.from(`${id}:${pass}`).toString("base64")}` : "";
  }

  const send = (res, status, body, type = "application/json") => {
    res.statusCode = status;
    res.setHeader("Content-Type", type);
    res.setHeader("Cache-Control", type === "image/jpeg" ? "private, max-age=3600" : "no-cache");
    res.end(type === "application/json" ? JSON.stringify(body) : body);
  };

  const storedRaw = (id) => (storedDir ? path.join(storedDir, id, "matterport", "raw_floors.json") : null);
  async function loadPlan(s) {
    const auth = matterportAuth();
    if (auth) return { plan: withPlanExtras(await generar(s.matterportModelId, auth)), source: "live" };
    const f = storedRaw(s.id);
    if (f && fs.existsSync(f)) return { plan: withPlanExtras(planDesdeRespuesta(s.matterportModelId, JSON.parse(fs.readFileSync(f, "utf8")))), source: "stored" };
    throw new MPError("Matterport API token is not set and no stored plan exists for this sample", 503);
  }
  // scan dates: stored data/raw/<id>/matterport/scan_meta.json, else (star buildings only) a read-only API query
  const heroDir = (id) => (storedDir && fs.existsSync(path.join(storedDir, id)) ? path.join(storedDir, id) : null);
  async function loadScan(s) {
    const dir = heroDir(s.id);
    if (!dir || !s.matterportModelId) return null; // samples without stored data: "Scan date: not available"
    const f = path.join(dir, "matterport", "scan_meta.json");
    if (fs.existsSync(f)) { const m = JSON.parse(fs.readFileSync(f, "utf8")); return { created: m.created ?? null, modified: m.modified ?? null, source: "stored" }; }
    const auth = matterportAuth();
    if (!auth) return null;
    const m = await fechasModelo(s.matterportModelId, auth);
    return m ? { ...m, source: "live" } : null;
  }
  // listing record (star buildings): only the fields the house view uses; no agent, office or media data
  function listingRecord(s) {
    const dir = heroDir(s.id), f = dir && path.join(dir, "costar", "listing.json");
    if (!f || !fs.existsSync(f)) return null;
    const j = JSON.parse(fs.readFileSync(f, "utf8")), p = j.property || {}, a = p.address || {};
    const ff = path.join(dir, "costar", "listing_features.json"), feats = fs.existsSync(ff) ? JSON.parse(fs.readFileSync(ff, "utf8")) : null;
    return {
      listing_id: String(j.id), stories: j.resoValues?.structure?.stories ?? null, state: a.state ?? null, zip: a.zip ?? null,
      street: a.fullStreetAddress ?? null, city: a.city ?? null, mls_property_type: p.mlsPropertyType ?? null,
      baths_full: p.bathsFull ?? null, baths_half: p.bathsHalf ?? null, remarks: j.remarks ?? null,
      features: (feats?.features || []).map((x) => ({ name: x.name, category: x.category ?? null, subcategory1: x.subcategory1 ?? null, subcategory2: x.subcategory2 ?? null })),
    };
  }
  async function sampleDoc(s) {
    if (!s.matterportModelId) { // building without an interior scan: no plan, never a guessed one
      const { id, address, location, city, market, listing } = s;
      return { id, address, location: location ? { ...location, city, market } : null, matterportModelId: null, listing: listing ?? null, listing_record: null, scan: null, no_scan: true, plan: null, quality: null, stairs: null };
    }
    // plan (live API, or the stored raw response without credentials) + extras computed once (plan-extras.mjs)
    const { plan, source } = await memo(`plan:${s.id}`, () => loadPlan(s));
    const scan = await memo(`scan:${s.id}`, () => loadScan(s)).catch(() => null);
    const { quality, stairs } = evaluar(plan, { sqft: s.listing?.sqft ?? null, beds: s.listing?.beds ?? null });
    const { id, address, location, city, market, matterportModelId, listing } = s;
    return { id, address, location: { ...location, city, market }, matterportModelId, listing, listing_record: listingRecord(s), scan, plan_source: source, quality, stairs, plan };
  }
  function limited(req) {
    const xff = trustProxy && String(req.headers?.["x-forwarded-for"] || "").split(",").map((x) => x.trim()).filter(Boolean).pop();
    const ip = xff || req.socket?.remoteAddress || "local", now = Date.now(), h = hits.get(ip);
    if (!h || now - h.t0 > RATE.windowMs) { hits.set(ip, { t0: now, n: 1 }); if (hits.size > 10000) hits.clear(); return 0; }
    h.n++;
    return h.n > RATE.max ? Math.ceil((h.t0 + RATE.windowMs - now) / 1000) : 0;
  }

  return async function handle(req, res, next) {
    const url = new URL(req.url ?? "/", "http://local");
    if (req.method !== "GET" || !url.pathname.startsWith(PREFIX)) return next();
    const [kind, id, file, ...extra] = url.pathname.slice(PREFIX.length).replace(/^\/+|\/+$/g, "").split("/");
    if (kind !== "sample" && kind !== "vexcel" && kind !== "plan") return next();
    const wait = limited(req);
    if (wait) { res.setHeader("Retry-After", String(wait)); return send(res, 429, { error: "too many requests" }); }
    try {
      if (!ID_RE.test(id || "")) return send(res, 404, { error: "unknown sample" });
      const s = findSample(id);
      if (!s || extra.length || ((kind === "sample" || kind === "plan") && file)) return send(res, 404, { error: "unknown sample" });
      if (kind === "sample") return send(res, 200, await sampleDoc(s));
      if (kind === "plan") {
        if (!s.matterportModelId) return send(res, 404, { error: "no interior scan for this sample" });
        const { plan, source } = await memo(`plan:${s.id}`, () => loadPlan(s));
        return send(res, 200, { id: s.id, model_id: s.matterportModelId, plan_source: source, plan, plan_index: buildPlanIndex(plan) });
      }
      if (!s.location) return send(res, 404, { error: "no location for this sample" });
      const vx = await memo(`vexcel:${s.id}`, () => obliques(secret("VEXCEL_API_KEY"), s.location.lat, s.location.lon));
      if (!file) { const { source, copyright, convention, views } = vx.meta; return send(res, 200, { source, copyright, convention, views }); }
      const m = /^(?:oblique-(north|east|south|west)|(aerial))\.jpg$/.exec(file);
      const img = m && vx.images[m[1] || m[2]];
      return img ? send(res, 200, img, "image/jpeg") : send(res, 404, { error: "no image for this view" });
    } catch (e) {
      // messages never include credentials
      const known = e instanceof MPError || e instanceof VXError;
      return send(res, known ? e.status || 502 : 500, { error: known ? e.message : `unexpected error: ${e.name}` });
    }
  };
}
