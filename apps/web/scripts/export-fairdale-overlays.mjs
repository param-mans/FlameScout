// Exports the viewer's debug scenario S4 (Fairdale, 209217448), versions 1 and 2, as plain overlay JSON with the real
// plan ids, for the WebSocket mock (scripts/mock_incident_ws.py) and the end-to-end test (overlay-e2e.mjs).
//   node apps/web/scripts/export-fairdale-overlays.mjs [--check]
// Offline: the plan is built from the stored raw API response, data/raw/209217448/matterport/raw_floors.json (local, not
// in git), exactly as the house-data server does without credentials. Each overlay is validated with incident/validate.js
// against the plan index. --check validates the committed files without writing them.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { planDesdeRespuesta } from "../server/matterport-plan.mjs";
import { withPlanExtras } from "../server/plan-extras.mjs";
import { buildPlanIndex } from "../public/house-viewer/incident/plan-index.js";
import { scenariosFor } from "../public/house-viewer/incident/scenarios.js";
import { validate } from "../public/house-viewer/incident/validate.js";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HOUSE = "209217448";
const OUT = path.join(WEB, "public", "house-viewer", "incident", "examples");
const catalog = JSON.parse(fs.readFileSync(path.join(WEB, "public", "house-viewer", "samples", "index.json"), "utf8"));
const modelId = catalog.find((s) => s.id === HOUSE).matterportModelId;
const raw = path.join(WEB, "..", "..", "data", "raw", HOUSE, "matterport", "raw_floors.json");
if (!fs.existsSync(raw)) { console.log(`SKIP: ${path.relative(process.cwd(), raw)} is missing (data/raw is local only)`); process.exit(0); }

const plan = withPlanExtras(planDesdeRespuesta(modelId, JSON.parse(fs.readFileSync(raw, "utf8"))));
const index = buildPlanIndex(plan);
const s4 = scenariosFor(HOUSE, modelId, plan, index).find((s) => s.key === "S4");
if (!s4 || s4.versions.length < 2) { console.log("FAIL: scenario S4 with two versions not available for Fairdale"); process.exit(1); }

let fail = 0;
for (const [i, ov] of s4.versions.slice(0, 2).entries()) {
  const file = path.join(OUT, `fairdale_s4_v${i + 1}.json`);
  const text = JSON.stringify(ov, null, 2) + "\n";
  if (process.argv.includes("--check")) {
    if (!fs.existsSync(file) || fs.readFileSync(file, "utf8") !== text) { fail++; console.log(`FAIL: ${path.basename(file)} differs from scenario S4 v${i + 1}`); }
  } else { fs.mkdirSync(OUT, { recursive: true }); fs.writeFileSync(file, text); }
  const errors = validate(JSON.parse(fs.readFileSync(file, "utf8")), index);
  if (errors.length) { fail++; console.log(`FAIL: ${path.basename(file)}:`, errors); } else console.log(`OK ${path.basename(file)} (v${ov.version}, model ${ov.model_id}) validates against the plan index`);
}
process.exit(fail ? 1 : 0);
