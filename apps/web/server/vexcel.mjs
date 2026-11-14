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

/** { meta: { source, copyright, convention, views: { dir: { file, captureDate } }, notes }, images: { dir: Buffer } } */
export async function obliques(key, lat, lon) {
  const point = { wkt: `POINT(${lon} ${lat})`, srid: "4326" };
  const cov = await call(key, "/oriented/collections", { ...point, "product-type": "oblique",
    include: "layer,collection,first-capture-date,last-capture-date,product-type" });
  const cols = cov.status === 200 ? (cov.body?.features ?? []).map((f) => f.properties ?? {}) : [];
  const meta = { source: "Vexcel API 2.0", copyright: COPYRIGHT, convention: "oblique-<dir> = direction the camera faces",
    views: {}, notes: cov.status === 200 ? [] : [`oriented/collections HTTP ${cov.status}`] };
  const images = {};
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
