// House catalog (data/raw/catalog.csv, built once by scripts/build_house_catalog.mjs, git-ignored): metadata of every
// scanned house that passed the plan quality check, keyed by CoStar residential listing id. The house data handler serves
// these ids in addition to public/house-viewer/samples/index.json (still only catalog ids: never an arbitrary model id).
import fs from "node:fs";

/** Minimal RFC 4180 CSV parser (quoted fields, doubled quotes, commas and newlines inside quotes) -> array of objects. */
export function parseCsv(text) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; } else field += c; continue; }
    if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") { if (c === "\r" && text[i + 1] === "\n") i++; row.push(field); rows.push(row); row = []; field = ""; }
    else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const [head = [], ...body] = rows.filter((r) => r.length > 1 || r[0]);
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
}

const num = (v) => (v === "" || v == null || !Number.isFinite(Number(v)) ? null : Number(v));

/** A catalog row as a sample entry (the shape of samples/index.json). No location: the catalog keeps no coordinates. */
export function catalogSample(row) {
  return {
    id: row.listing_id, address: [row.address, row.city].filter(Boolean).join(", "), location: null,
    city: row.city || null, market: row.city || null, matterportModelId: row.matterport_model_id || null,
    listing: { beds: num(row.bedrooms), baths: num(row.bathrooms), sqft: num(row.sqft), yearBuilt: num(row.year_built), homeType: null },
    catalog: true,
  };
}

/** Lookup by listing id over a CSV file, re-read only when it changes (mtime + size). Missing file = empty catalog. */
export function createCatalogLookup(file) {
  let key = null, byId = new Map();
  return function lookup(id) {
    if (!file) return null;
    let st;
    try { st = fs.statSync(file); } catch { byId = new Map(); key = null; return null; }
    const k = `${st.mtimeMs}:${st.size}`;
    if (k !== key) {
      byId = new Map(parseCsv(fs.readFileSync(file, "utf8")).filter((r) => r.listing_id && r.matterport_model_id).map((r) => [r.listing_id, r]));
      key = k;
    }
    const row = byId.get(id);
    return row ? catalogSample(row) : null;
  };
}
