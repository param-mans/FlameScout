// Tests for the viewer's overlay validator (public/house-viewer/incident/validate.js) against the reference
// packages/schemas/overlay/validate_overlay.py.
//   node apps/web/server/tests/validate.test.mjs [--cases N]
// 1) the two valid examples (scan and no scan), 2) the negative cases of tests/test_validate_overlay.py,
// 3) differential check: N seeded random mutations of the examples, the error lists must equal Python's exactly
//    (skipped if python3 is not available). Exit code 1 on any failure.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { validate, isoOk, DIRECTIVE } from "../../public/house-viewer/incident/validate.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");
const OV_DIR = path.join(ROOT, "packages", "schemas", "overlay");
const load = (n) => JSON.parse(fs.readFileSync(path.join(OV_DIR, n), "utf8"));
const OVERLAY = load("incident_overlay.example.json"), PLAN = load("plan_index.example.json");
const NOSCAN_OVERLAY = load("incident_overlay.noscan.example.json"), NOSCAN_PLAN = load("plan_index.noscan.example.json");
let fail = 0;
const ok = (cond, msg) => { if (!cond) { fail++; console.log("FAIL", msg); } };
const mut = (base, f) => { const o = structuredClone(base); f(o); return o; };

// 1) valid examples
ok(validate(OVERLAY, PLAN).length === 0, "example must be valid: " + JSON.stringify(validate(OVERLAY, PLAN)));
ok(validate(NOSCAN_OVERLAY, NOSCAN_PLAN).length === 0, "no-scan example must be valid");
ok(validate(mut(NOSCAN_OVERLAY, (o) => { o.fire[0].location = { kind: "room", room_id: "r1" }; }), NOSCAN_PLAN).some((e) => e.includes("does not exist in the plan")), "no-scan rejects room ids");

// 2) negative cases (same as tests/test_validate_overlay.py)
const CASES = [
  ["invented room id", (o) => { o.occupants[0].location.room_id = "r99"; }, "does not exist in the plan"],
  ["coordinates in output", (o) => { o.fire[1].location.x = 3.2; }, "coordinates are not allowed"],
  ["no evidence", (o) => { o.fire[0].evidence_ids = []; }, "evidence_ids is required"],
  ["confidence out of range", (o) => { o.fire[0].confidence = 1.4; }, "confidence must be"],
  ["directive wording", (o) => { o.considerations[0].text = "Consider this but you must go now"; }, "directive wording"],
  ["not advisory", (o) => { o.considerations[0].text = "Send a crew upstairs"; }, "start with 'Consider'"],
  ["group without contradiction entry", (o) => { o.contradictions = []; }, "has no entry in contradictions"],
  ["group member not contradicted", (o) => { o.occupants[0].status = "reported"; }, "must have status 'contradicted'"],
  ["route made by the agent", (o) => { o.routes[0].produced_by = "agent"; }, "produced_by must be 'planner'"],
  ["waypoint does not exist", (o) => { o.routes[0].waypoints[3].ref_id = "s9"; }, "does not exist"],
  ["anchor ref does not exist", (o) => { o.fire[1].location.anchor.ref_id = "w99"; }, "anchor.ref_id"],
  ["objective target does not resolve", (o) => { o.objectives[0].target.ref_id = "nope"; }, "does not resolve"],
  ["duplicate id", (o) => { o.hazards[0].id = "fire-1"; }, "duplicate id"],
  ["cardinal anchor (no orientation in the contract)", (o) => { o.fire[1].location.anchor.position = "north_side"; }, "anchor.position must be one of"],
];
for (const [name, f, expect] of CASES) {
  const errs = validate(mut(OVERLAY, f), PLAN);
  ok(errs.some((e) => e.includes(expect)), `${name}: expected ${JSON.stringify(expect)} in ${JSON.stringify(errs)}`);
}

// 3) differential check against Python
const N = Number(process.argv[process.argv.indexOf("--cases") + 1]) || 3000;
let seed = 20261114;
const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
const pick = (a) => a[Math.floor(rnd() * a.length)];
const VALUES = [null, "", 0, 1, 2, -1, 0.5, 1.4, true, false, [], {}, "r99", "r9", "r4", "w7", "d1", "s1", "f1", "f2", "f9", "occ-loc-1", "fire-1",
  "room", "anchor", "floor", "unknown", "occupant", "occupant_group", "near_window", "near_stairs", "center", "north_side", "west_side", "planner", "agent",
  "reported", "contradicted", "unverified", "verified", "smoke", "gas", "rescue", "recon", "exterior", "stairs",
  "Consider it", "Consider this, it is guaranteed", "Consider 100% now", "Consider 100%x", "Consider musty air", "Send a crew",
  "2026-11-15T03:10:05Z", "2026-11-15", "20261115T031005", "2026-W46-3T03:10", "2026-11-15 03:10:05.123456+01:00", "2026-02-30T00:00:00Z",
  "2026-11-15T24:00:00Z", "2026-11-15T", "yesterday", ["call-1:1-2"], [1, 2], [2, 1], [0, 3], { kind: "floor", floor_reported: 2 },
  { kind: "room", room_id: "r10" }, { kind: "unknown", room_id: "r9" }, { kind: "anchor", anchor: { room_id: "r4", position: "near_door", ref_id: "d2" } }];
const NEW_KEYS = ["x", "lat", "points", "group_id", "floor_id", "floor_reported", "ref_id", "count", "count_range", "note", "extra"];
function paths(o, p = []) {
  const out = [];
  if (o && typeof o === "object") for (const k of Object.keys(o)) { out.push([...p, Array.isArray(o) ? Number(k) : k]); out.push(...paths(o[k], [...p, Array.isArray(o) ? Number(k) : k])); }
  return out;
}
function mutate(base) {
  const o = structuredClone(base);
  const n = 1 + Math.floor(rnd() * 3);
  for (let i = 0; i < n; i++) {
    const ps = paths(o);
    if (!ps.length) break;
    const p = pick(ps), parent = p.slice(0, -1).reduce((a, k) => a[k], o), k = p[p.length - 1];
    const r = rnd();
    if (r < 0.55) parent[k] = structuredClone(pick(VALUES));
    else if (r < 0.75) { if (Array.isArray(parent)) parent.splice(k, 1); else delete parent[k]; }
    else if (r < 0.9 && parent[k] && typeof parent[k] === "object" && !Array.isArray(parent[k])) parent[k][pick(NEW_KEYS)] = structuredClone(pick(VALUES));
    else if (Array.isArray(parent)) parent.push(structuredClone(parent[k]));
  }
  return o;
}
const py = spawnSync("python3", ["--version"]);
if (py.status !== 0) console.log("SKIP differential check: python3 not available");
else {
  const cases = [];
  for (let i = 0; i < N; i++) { const scan = rnd() < 0.8; cases.push({ plan: scan ? "scan" : "noscan", overlay: mutate(scan ? OVERLAY : NOSCAN_OVERLAY) }); }
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(OV_DIR)})
import validate_overlay as v
plans = {"scan": json.load(open(${JSON.stringify(path.join(OV_DIR, "plan_index.example.json"))})), "noscan": json.load(open(${JSON.stringify(path.join(OV_DIR, "plan_index.noscan.example.json"))}))}
out = []
for line in sys.stdin:
    c = json.loads(line)
    try: out.append(v.validate(c["overlay"], plans[c["plan"]]))
    except Exception as e:
        import traceback; tb = traceback.extract_tb(e.__traceback__)[-1]; out.append("CRASH: %s: %s (line %d: %s)" % (type(e).__name__, e, tb.lineno, tb.line))
print(json.dumps(out))`;
  const r = spawnSync("python3", ["-B", "-c", script], { input: cases.map((c) => JSON.stringify(c)).join("\n"), maxBuffer: 1 << 28 });
  if (r.status !== 0) { fail++; console.log("FAIL python harness:", r.stderr.toString().slice(0, 400)); }
  else {
    const pyRes = JSON.parse(r.stdout.toString());
    let same = 0, crashes = 0, diffs = 0;
    cases.forEach((c, i) => {
      const js = validate(c.overlay, c.plan === "scan" ? PLAN : NOSCAN_PLAN), p = pyRes[i];
      if (typeof p === "string") { crashes++; if (!js.length) { diffs++; if (diffs <= 10) console.log("DIFF python crashed but JS accepted:", p); } return; }
      if (JSON.stringify(js) === JSON.stringify(p)) same++;
      else { diffs++; if (diffs <= 5) console.log("DIFF case", i, "\n  js:", JSON.stringify(js), "\n  py:", JSON.stringify(p)); }
    });
    console.log(`differential: ${N} cases, ${same} identical error lists, ${crashes} python crashes (rejected by JS too), ${diffs} differences`);
    if (diffs) fail++;
  }
}
// 4) timestamps: isoOk must agree with Python's datetime.fromisoformat(str(x).replace("Z", "+00:00"))
if (py.status === 0) {
  const D = ["2026-11-15", "20261115", "2026-W46", "2026W46", "2026-W46-3", "2026W463", "2026-02-29", "2024-02-29", "2026-13-01", "2026-11-31", "2026-W53", "2020-W53-7", "0000-01-01", "2026-1-15", "2026-11"];
  const SEP = ["T", " ", "x", "", "TT"];
  const T = ["", "03", "0310", "03:10", "031005", "03:10:05", "03:10:05.1", "03:10:05,123456789", "03:1005", "24:00", "23:59:60", "3:10", "03:10:05."];
  const Z = ["", "Z", "+00:00", "-05:00", "+0530", "+05", "+5", "+23:59", "+24:00", "+01:60", "-00:30:15", "+01:00:00.5", "+"];
  const strs = [];
  for (const d of D) for (const sp of SEP) for (const t of T) for (const z of Z) strs.push(d + (t || z ? sp : "") + t + z);
  strs.push("", "None", "2026-11-15T03:10:05Z", "yesterday", "2026-11-15T03:10:05ZZ");
  const r = spawnSync("python3", ["-B", "-c", `
import json, sys
from datetime import datetime
out = []
for s in json.loads(sys.stdin.read()):
    try: datetime.fromisoformat(str(s).replace("Z", "+00:00")); out.append(True)
    except ValueError: out.append(False)
print(json.dumps(out))`], { input: JSON.stringify(strs), maxBuffer: 1 << 26 });
  const pyOk = JSON.parse(r.stdout.toString());
  const bad = strs.filter((x, i) => isoOk(x) !== pyOk[i]);
  console.log(`timestamps: ${strs.length} strings, ${bad.length} disagreements` + (bad.length ? " e.g. " + JSON.stringify(bad.slice(0, 8)) : ""));
  if (bad.length) fail++;
}
// 5) directive wording: DIRECTIVE.test must agree with Python's re.search(r"\b(...)\b", re.I) on every string
if (py.status === 0) {
  const words = ["must", "MUST", "Must", "immediately", "do it now", "do  it now", "ordered", "100%", "100 %", "100\t%", "100%x", "guaranteed", "ſuſt"];
  const around = ["", " ", "x", "_", "1", "é", "ñ", "-", ".", "%", "日", "𝒜", "\n"];
  const strs = [];
  for (const w of words) for (const a of around) for (const b of around) strs.push(a + w + b);
  strs.push("Consider this but you must go now", "mustmust", "a must-have", "musty", "admust", "100%%", "1100%", "x100% y", "Consider 100% sure", "ordered.", "disordered", "guaranteed!");
  const r = spawnSync("python3", ["-B", "-c", `
import json, re, sys
D = re.compile(r"\\b(must|immediately|do it now|ordered|100\\s*%|guaranteed)\\b", re.I)
print(json.dumps([bool(D.search(s)) for s in json.loads(sys.stdin.read())]))`], { input: JSON.stringify(strs), maxBuffer: 1 << 24 });
  const pyD = JSON.parse(r.stdout.toString()), bad = strs.filter((x, i) => DIRECTIVE.test(x) !== pyD[i]);
  console.log(`directive wording: ${strs.length} strings, ${bad.length} disagreements` + (bad.length ? " e.g. " + JSON.stringify(bad.slice(0, 8)) : ""));
  if (bad.length) fail++;
}
console.log(fail ? `${fail} FAILED` : "OK all validator tests");
process.exit(fail ? 1 : 0);
