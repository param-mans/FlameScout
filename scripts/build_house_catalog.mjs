// House catalog: one metadata row per scanned house that passed the plan quality check, for the address locator
// (services/agents/house_catalog.py) and the house data service (apps/web/server/handler.mjs, which serves only
// catalog ids). Metadata only: no plans, no geometry, no remarks. Run once, locally:
//   node scripts/build_house_catalog.mjs [--demo ~/Desktop/demo_costar] [-o data/raw/catalog.csv]
// Sources (demo_costar, not in this repo):
//   web/data/calidad.csv                      id = Matterport model id, estado ok|descartada, anuncios = listing id(s) a|b
//   costar-hackathon-kit 2/data/residential-listings.jsonl   address, city, state, zip, beds, baths, sqft, year built
//   web/plans/gen/<model>.json                plan, ONLY summarized here (floors, room types, stairs, exterior doors);
//                                             stairs and exterior doors with the viewer's own logic (plan-extras.mjs)
// The output (data/raw/catalog.csv) is git-ignored like the rest of data/raw.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import { withPlanExtras } from "../apps/web/server/plan-extras.mjs";
import { parseCsv } from "../apps/web/server/house-catalog.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const arg = (name, dflt) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : dflt; };
const DEMO = arg("--demo", path.join(os.homedir(), "Desktop", "demo_costar"));
const OUT = path.resolve(arg("-o", path.join(ROOT, "data", "raw", "catalog.csv")));
const COLUMNS = ["listing_id", "matterport_model_id", "address", "city", "state", "zip", "floors", "bedrooms", "bathrooms",
  "sqft", "year_built", "room_types", "exterior_doors", "stairs", "quality"];

const cell = (v) => { const s = v == null ? "" : String(v); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };

async function listings(file, wanted) {
  const out = new Map();
  const rl = readline.createInterface({ input: fs.createReadStream(file), crlfDelay: Infinity });
  for await (const line of rl) {
    const m = /^\{"id":\s*(\d+)/.exec(line);
    if (!m || !wanted.has(m[1])) continue;
    const j = JSON.parse(line), p = j.property || {}, a = p.address || {};
    out.set(String(j.id), {
      listDate: j.listDate || "", address: a.fullStreetAddress || "", city: a.city || "", state: a.state || "", zip: a.zip || "",
      bedrooms: p.bedsMin ?? "", bathrooms: p.bathsFull != null ? p.bathsFull + 0.5 * (p.bathsHalf || 0) : "",
      sqft: p.sqftMin ?? "", year_built: p.yearBuilt ?? "",
    });
  }
  return out;
}

function summarize(plan) {
  const full = withPlanExtras(plan);
  const floors = full.floors.filter((f) => (f.rooms || []).some((r) => r.type !== "Void"));
  const types = new Set();
  for (const f of floors) for (const r of f.rooms) if (r.type && r.type !== "Void") types.add(String(r.type).toLowerCase());
  const doors = full.floors.reduce((n, f) => n + (f.openings || []).filter((o) => o.type === "doorway" && o.exterior).length, 0);
  return { floors: floors.length, room_types: [...types].sort().join("|"), exterior_doors: doors, stairs: full.stairs.length };
}

const quality = parseCsv(fs.readFileSync(path.join(DEMO, "web", "data", "calidad.csv"), "utf8"));
const ok = quality.filter((r) => r.estado === "ok");
const skipped = { not_ok: quality.length - ok.length, no_listing_id: 0, listing_not_in_kit: 0, no_plan: 0, plan_error: 0, duplicate_listing: 0 };
const wanted = new Set(ok.flatMap((r) => r.anuncios.split("|").map((x) => x.trim()).filter(Boolean)));
const kit = await listings(path.join(DEMO, "costar-hackathon-kit 2", "data", "residential-listings.jsonl"), wanted);

const rows = [], seen = new Set();
for (const q of ok) {
  const ids = q.anuncios.split("|").map((x) => x.trim()).filter(Boolean);
  if (!ids.length) { skipped.no_listing_id++; continue; }
  // several listings of the same house: the most recent one in the kit
  const found = ids.filter((id) => kit.has(id)).sort((a, b) => kit.get(b).listDate.localeCompare(kit.get(a).listDate));
  if (!found.length) { skipped.listing_not_in_kit++; continue; }
  const id = found[0];
  if (seen.has(id)) { skipped.duplicate_listing++; continue; }
  const planFile = path.join(DEMO, "web", "plans", "gen", `${q.id}.json`);
  if (!fs.existsSync(planFile)) { skipped.no_plan++; continue; }
  let s;
  try { s = summarize(JSON.parse(fs.readFileSync(planFile, "utf8"))); } catch (e) { skipped.plan_error++; console.error(`plan ${q.id}: ${e.message}`); continue; }
  const l = kit.get(id);
  seen.add(id);
  rows.push({ listing_id: id, matterport_model_id: q.id, address: l.address, city: l.city, state: l.state, zip: l.zip,
    floors: s.floors, bedrooms: l.bedrooms, bathrooms: l.bathrooms, sqft: l.sqft, year_built: l.year_built,
    room_types: s.room_types, exterior_doors: s.exterior_doors, stairs: s.stairs, quality: q.etiquetas ? `ok|${q.etiquetas}` : "ok" });
}
rows.sort((a, b) => a.listing_id.localeCompare(b.listing_id));
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, [COLUMNS.join(","), ...rows.map((r) => COLUMNS.map((c) => cell(r[c])).join(","))].join("\n") + "\n");
console.log(`catalog: ${rows.length} houses -> ${path.relative(ROOT, OUT)} (${fs.statSync(OUT).size} bytes)`);
console.log(`skipped: ${JSON.stringify(skipped)}`);
