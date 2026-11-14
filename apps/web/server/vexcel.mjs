// Vexcel oblique aerial views (N / E / S / W) of a point, from Vexcel API 2.0. Read-only GET requests: one coverage query
// plus one extract per direction. The key travels only in the X-API-KEY header (the API's "Header" scheme), never in a
// URL, log or response. Results are kept in memory only (no files on disk).
// Convention (checked visually on a known house): product-type oblique-<dir> = direction the camera faces.
const BASE = "https://api.vexcelgroup.com/v2";
export const DIRS = ["north", "east", "south", "west"];
const LAYERS = ["urban", "wide-area"]; // urban for cities, wide-area as regional coverage
const BOX_M = 90; // side (m) of the box requested for each oblique view
const COPYRIGHT = "© Vexcel Imaging";

export class VXError extends Error {
  constructor(message, status = 502) { super(message); this.status = status; }
}

async function call(key, route, params, binary = false) {
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

const ZOOM = 20, PX = 1024;
// side (m) that PX pixels cover at a zoom level (Web Mercator), as the demo's metros_para_px
const metersForPx = (lat, zoom) => Math.round((PX * 156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom * 10) / 10;

/** Straight-down aerial (ortho): coverage first (layer urban, else wide-area), zoom 20 or the maximum available. */
async function ortho(key, lat, lon, point) {
  const cov = await call(key, "/ortho/collections", { ...point, include: "layer,collection,first-capture-date,last-capture-date,max-zoom" });
  const cols = cov.status === 200 ? (cov.body?.features ?? []).map((f) => f.properties ?? {}) : [];
  const newest = (xs) => [...xs].sort((a, b) => String(b["last-capture-date"] ?? "").localeCompare(String(a["last-capture-date"] ?? "")))[0];
  const pick = LAYERS.map((l) => newest(cols.filter((c) => c.layer === l))).find(Boolean) || newest(cols);
  if (!pick) return { view: null, image: null, note: cov.status === 200 ? "no ortho coverage" : `ortho/collections HTTP ${cov.status}` };
  const zoom = pick["max-zoom"] && pick["max-zoom"] < ZOOM ? Math.floor(pick["max-zoom"]) : ZOOM, side = metersForPx(lat, zoom);
  const { status, body } = await call(key, "/ortho/extract", { layer: pick.layer || "urban", ...point, bands: "rgb", zoom, "bbox-dimensions": `${side},${side}`,
    "image-format": "jpeg", ...(pick.collection ? { collection: pick.collection } : {}) }, true);
  if (status === 200 && body && body[0] === 0xff && body[1] === 0xd8)
    return { view: { file: "aerial.jpg", captureDate: pick["last-capture-date"] ?? pick["first-capture-date"] ?? null, layer: pick.layer ?? null, boxMeters: side }, image: body };
  return { view: null, image: null, note: `ortho/extract HTTP ${status}` };
}

/** { meta: { source, copyright, convention, views: { aerial | dir: { file, captureDate } }, notes }, images: { aerial | dir: Buffer } } */
export async function obliques(key, lat, lon) {
  const point = { wkt: `POINT(${lon} ${lat})`, srid: "4326" };
  const top = await ortho(key, lat, lon, point);
  const cov = await call(key, "/oriented/collections", { ...point, "product-type": "oblique",
    include: "layer,collection,first-capture-date,last-capture-date,product-type" });
  const cols = cov.status === 200 ? (cov.body?.features ?? []).map((f) => f.properties ?? {}) : [];
  const meta = { source: "Vexcel API 2.0", copyright: COPYRIGHT, convention: "oblique-<dir> = direction the camera faces",
    views: {}, notes: cov.status === 200 ? [] : [`oriented/collections HTTP ${cov.status}`] };
  const images = {};
  if (top.view) { meta.views.aerial = top.view; images.aerial = top.image; } else meta.notes.push(top.note);
  for (const d of DIRS) {
    const pt = `oblique-${d}`;
    const cand = cols.filter((c) => [pt, "oblique", undefined].includes(c["product-type"]));
    const pool = cand.length ? cand : cols;
    const layer = LAYERS.find((l) => pool.some((c) => c.layer === l));
    const params = { ...point, "product-type": pt, bands: "rgb", "bbox-dimensions": `${BOX_M},${BOX_M}`, "image-format": "jpeg",
      // newest collection and, within it, the photo with the house closest to the center
      "order-by": "collection-last-capture-date-desc,image-center-distance-asc", ...(layer ? { layer } : {}) };
    const { status, body } = await call(key, "/oriented/extract", params, true);
    if (status === 200 && body && body[0] === 0xff && body[1] === 0xd8) {
      images[d] = body;
      const info = cols.filter((c) => c["product-type"] === pt).sort((a, b) => String(b["last-capture-date"] ?? "").localeCompare(String(a["last-capture-date"] ?? "")));
      meta.views[d] = { file: `${pt}.jpg`, captureDate: info[0] ? info[0]["last-capture-date"] ?? info[0]["first-capture-date"] ?? null : null };
    } else meta.notes.push(`oriented/extract ${pt} HTTP ${status}`);
  }
  return { meta, images };
}
