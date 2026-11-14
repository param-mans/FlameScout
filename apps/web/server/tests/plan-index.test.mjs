// The viewer's plan index must be identical to the stored derived index (docs/INCIDENT_OVERLAY_SPEC.md, "Ids").
//   node apps/web/server/tests/plan-index.test.mjs           # offline (default): no keys, no network
//   LIVE=1 node apps/web/server/tests/plan-index.test.mjs    # live Matterport API (read-only), needs MP_ID + MP_SECRET
// For each star building, the plan is produced exactly as the viewer receives it (house-data handler plus plan extras):
// by default from the stored raw API response (data/raw/<id>/matterport/raw_floors.json), with LIVE=1 from the API.
// The index is built with the viewer's module (public/house-viewer/incident/plan-index.js) and compared with
// data/raw/<id>/derived/plan_index.json (built from plan.json by scripts/make_plan_index.py). Exit code 1 if any differs.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHouseDataHandler } from "../handler.mjs";
import { buildPlanIndex } from "../../public/house-viewer/incident/plan-index.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
const BUILDINGS = ["209217448", "214905148", "199958956"];
const LIVE = process.env.LIVE === "1";
if (!fs.existsSync(path.join(ROOT, "data", "raw"))) {
  console.log("SKIP: data/raw/ does not exist (sponsor data, not in git). Copy it into the repo to run this test.");
  process.exit(0);
}
const envFile = path.join(ROOT, ".env");
const env = fs.existsSync(envFile) ? Object.fromEntries(fs.readFileSync(envFile, "utf8").split("\n")
  .map((l) => l.match(/^\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*)$/)).filter(Boolean).map((m) => [m[1], m[2].trim().replace(/^['"]|['"]$/g, "")])) : {};
const getEnv = (k) => process.env[k] || env[k];
const id_ = getEnv("MP_ID") || getEnv("MATTERPORT_API_KEY"), secret_ = getEnv("MP_SECRET") || getEnv("MATTERPORT_API_SECRET");
if (LIVE && !(id_ && secret_)) {
  console.log("SKIP live check: MP_ID and MP_SECRET (or MATTERPORT_API_KEY / MATTERPORT_API_SECRET) are not set in the environment or .env");
  process.exit(0);
}
const handle = createHouseDataHandler({
  getEnv: LIVE ? getEnv : (k) => (/^(MP_|MATTERPORT_)/.test(k) ? undefined : getEnv(k)), // offline: force the stored plan
  catalogFile: path.join(ROOT, "apps", "web", "public", "house-viewer", "samples", "index.json"),
  storedDir: path.join(ROOT, "data", "raw") });
console.log(LIVE ? "mode: LIVE (Matterport API, read-only)" : "mode: offline (stored raw API responses; LIVE=1 for the live API)");

function fetchDoc(id) {
  return new Promise((resolve, reject) => {
    const res = { statusCode: 200, headers: {}, setHeader(k, v) { this.headers[k] = v; },
      end(body) { this.statusCode === 200 ? resolve(JSON.parse(body)) : reject(new Error(`HTTP ${this.statusCode}: ${body}`)); } };
    handle({ method: "GET", url: `/api/house-viewer/sample/${id}` }, res, () => reject(new Error("route not handled")));
  });
}
// deep difference (object key order is not meaningful in JSON; array order is)
function diff(a, b, p = "", out = []) {
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) out.push(`${p}: ${JSON.stringify(a)?.slice(0, 80)} != ${JSON.stringify(b)?.slice(0, 80)}`);
    else a.forEach((x, i) => diff(x, b[i], `${p}[${i}]`, out));
  } else if (a && b && typeof a === "object" && typeof b === "object") {
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) diff(a[k], b[k], `${p}.${k}`, out);
  } else if (a !== b) out.push(`${p}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
  return out;
}

let fail = 0;
for (const id of BUILDINGS) {
  const stored = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "raw", id, "derived", "plan_index.json"), "utf8"));
  let viewer;
  let source;
  try { const doc = await fetchDoc(id); source = doc.plan_source; viewer = buildPlanIndex(doc.plan); } catch (e) { fail++; console.log(`FAIL ${id}: ${e.message}`); continue; }
  const d = diff(viewer, stored);
  const n = (x) => `${x.floors.length} floors, ${Object.keys(x.rooms).length} rooms, ${Object.keys(x.openings).length} openings, ${x.stairs.length} stairs`;
  if (d.length) { fail++; console.log(`FAIL ${id}: viewer index differs from data/raw/${id}/derived/plan_index.json (${d.length} differences)`); d.slice(0, 8).forEach((x) => console.log("   ", x)); }
  else console.log(`OK ${id}: identical (${n(viewer)}; plan ${source})`);
}
console.log(fail ? `${fail} FAILED` : "OK viewer plan index == derived plan index for all buildings");
process.exit(fail ? 1 : 0);
