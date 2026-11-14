// Vite plugin: Vexcel oblique aerial views (N / E / S / W) for the house viewer, served by the Vite server itself
// (`npm run dev` and `npm run preview`), so no separate process is needed.
//
//   GET /api/house-viewer/vexcel/<sample>                     -> { views: { dir: { file, captureDate } }, ... }
//   GET /api/house-viewer/vexcel/<sample>/oblique-<dir>.jpg   -> JPEG
//
// Coordinates come from public/house-viewer/samples/index.json. The key is VEXCEL_API_KEY from the environment or the
// repo's .env (gitignored), loaded only here on the server: it is never exposed to client code (no VITE_ prefix, no
// define) and travels only in the X-API-KEY header (Vexcel API 2.0 "Header" scheme), never in a URL, log or response.
// Images are cached in <repo>/data/cache/vexcel/<sample>/ (gitignored; Vexcel imagery is licensed, never commit it).
// Read-only GET requests to Vexcel: one coverage query plus one extract per direction, only on a cache miss.
// Convention (checked visually on a known house): product-type oblique-<dir> = direction the camera faces.
import fs from "node:fs";
import path from "node:path";
import { loadEnv } from "vite";

const BASE = "https://api.vexcelgroup.com/v2";
const PREFIX = "/api/house-viewer/vexcel/";
const DIRS = ["north", "east", "south", "west"];
const LAYERS = ["urban", "wide-area"]; // urban for cities, wide-area as regional coverage
const BOX_M = 90; // side (m) of the box requested for each oblique view
const COPYRIGHT = "© Vexcel Imaging";

class VXError extends Error {
  constructor(message, status = 502) { super(message); this.status = status; }
}

export function vexcelImagery() {
  let key = "", samplesFile = "", cacheDir = "";
  const pending = new Map(); // sample -> Promise<meta>, so concurrent requests share one fetch

  async function call(route, params, binary = false) {
    if (!key) throw new VXError("VEXCEL_API_KEY is not set (add it to .env)", 503);
    const url = `${BASE}${route}?${new URLSearchParams(params)}`;
    for (let attempt = 0; attempt < 3; attempt++) {
      let r;
      try {
        r = await fetch(url, { headers: { "X-API-KEY": key, Accept: "*/*" }, signal: AbortSignal.timeout(90000) });
      } catch (e) {
        throw new VXError(`cannot reach Vexcel (${e.name})`);
      }
      if (r.status === 429 && attempt < 2) { await new Promise((ok) => setTimeout(ok, 1000 * Number(r.headers.get("retry-after") || 5))); continue; }
      if (r.status === 401 || r.status === 403) throw new VXError(`Vexcel rejected the key (HTTP ${r.status})`, r.status);
      if (!r.ok) return { status: r.status, body: null };
      return { status: r.status, body: binary ? Buffer.from(await r.arrayBuffer()) : await r.json().catch(() => null) };
    }
    return { status: 429, body: null };
  }

  async function fetchObliques(id, lat, lon, folder) {
    const point = { wkt: `POINT(${lon} ${lat})`, srid: "4326" };
    const cov = await call("/oriented/collections", { ...point, "product-type": "oblique",
      include: "layer,collection,first-capture-date,last-capture-date,product-type" });
    const cols = cov.status === 200 ? (cov.body?.features ?? []).map((f) => f.properties ?? {}) : [];
    fs.mkdirSync(folder, { recursive: true });
    const meta = { source: "Vexcel API 2.0", copyright: COPYRIGHT, convention: "oblique-<dir> = direction the camera faces",
      views: {}, notes: cov.status === 200 ? [] : [`oriented/collections HTTP ${cov.status}`] };
    for (const d of DIRS) {
      const pt = `oblique-${d}`;
      const cand = cols.filter((c) => [pt, "oblique", undefined].includes(c["product-type"]));
      const pool = cand.length ? cand : cols;
      const layer = LAYERS.find((l) => pool.some((c) => c.layer === l));
      const params = { ...point, "product-type": pt, bands: "rgb", "bbox-dimensions": `${BOX_M},${BOX_M}`, "image-format": "jpeg",
        // newest collection and, within it, the photo with the house closest to the center
        "order-by": "collection-last-capture-date-desc,image-center-distance-asc", ...(layer ? { layer } : {}) };
      const { status, body } = await call("/oriented/extract", params, true);
      if (status === 200 && body && body[0] === 0xff && body[1] === 0xd8) {
        fs.writeFileSync(path.join(folder, `${pt}.jpg`), body);
        const info = cols.filter((c) => c["product-type"] === pt).sort((a, b) => String(b["last-capture-date"] ?? "").localeCompare(String(a["last-capture-date"] ?? "")));
        meta.views[d] = { file: `${pt}.jpg`, captureDate: info[0] ? info[0]["last-capture-date"] ?? info[0]["first-capture-date"] ?? null : null };
      } else meta.notes.push(`oriented/extract ${pt} HTTP ${status}`);
    }
    meta.fetchedAt = new Date().toISOString();
    fs.writeFileSync(path.join(folder, "meta.json"), JSON.stringify(meta, null, 1));
    return meta;
  }

  function obliques(id, lat, lon) {
    const folder = path.join(cacheDir, id), metaFile = path.join(folder, "meta.json");
    if (fs.existsSync(metaFile)) return Promise.resolve(JSON.parse(fs.readFileSync(metaFile, "utf8")));
    if (!pending.has(id)) pending.set(id, fetchObliques(id, lat, lon, folder).finally(() => pending.delete(id)));
    return pending.get(id);
  }

  const send = (res, status, body, type = "application/json") => {
    res.statusCode = status;
    res.setHeader("Content-Type", type);
    res.setHeader("Cache-Control", type === "image/jpeg" ? "private, max-age=86400" : "no-cache");
    res.end(body);
  };

  async function handle(req, res, next) {
    const url = new URL(req.url ?? "/", "http://local");
    if (req.method !== "GET" || !url.pathname.startsWith(PREFIX)) return next();
    const parts = url.pathname.slice(PREFIX.length).replace(/^\/+|\/+$/g, "").split("/");
    try {
      const list = JSON.parse(fs.readFileSync(samplesFile, "utf8"));
      const s = list.find((x) => x.id === parts[0]);
      if (!s || parts.length > 2 || !s.location) return send(res, 404, JSON.stringify({ error: "unknown sample" }));
      const meta = await obliques(s.id, s.location.lat, s.location.lon);
      if (parts.length === 1) {
        const { source, copyright, convention, views } = meta;
        return send(res, 200, JSON.stringify({ source, copyright, convention, views }));
      }
      const m = /^oblique-(north|east|south|west)\.jpg$/.exec(parts[1]);
      const file = m && meta.views[m[1]] ? path.join(cacheDir, s.id, parts[1]) : null;
      if (!file || !fs.existsSync(file)) return send(res, 404, JSON.stringify({ error: "no image for this view" }));
      return send(res, 200, fs.readFileSync(file), "image/jpeg");
    } catch (e) {
      // messages never include the key
      return send(res, e instanceof VXError ? e.status : 500, JSON.stringify({ error: e instanceof VXError ? e.message : `unexpected error: ${e.name}` }));
    }
  }

  return {
    name: "flamescout-vexcel-imagery",
    configResolved(config) {
      const repo = path.resolve(config.root, "..", "..");
      key = (process.env.VEXCEL_API_KEY || loadEnv(config.mode, repo, "VEXCEL_").VEXCEL_API_KEY || "").trim();
      samplesFile = path.join(config.publicDir, "house-viewer", "samples", "index.json");
      cacheDir = path.join(repo, "data", "cache", "vexcel");
    },
    // middlewares added here run before Vite's own (including the /api proxy to :8000)
    configureServer(server) { server.middlewares.use(handle); },
    configurePreviewServer(server) { server.middlewares.use(handle); },
  };
}
