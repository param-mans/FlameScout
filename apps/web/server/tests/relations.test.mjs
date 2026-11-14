// Room size and spatial relations (plan-extras.mjs): synthetic houses made of rectangles, no network.
//   node apps/web/server/tests/relations.test.mjs
// Checks over / under / next_to / at_stairs / size_rank on a two-floor house, symmetry (over <-> under, next_to mutual),
// invariance when the house is turned 90, 180 and 270 degrees, and that a room with an unreliable outline stays out.
import { withPlanExtras } from "../plan-extras.mjs";

let fail = 0;
const ok = (cond, msg) => { if (!cond) { fail++; console.log("FAIL", msg); } };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// rectangle room [x0, y0, x1, y1]
const room = (id, type, [x0, y0, x1, y1], quality = "ok") => ({ id, label: "", type, areaM2: (x1 - x0) * (y1 - y0),
  polygon: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], polygonQuality: quality, bbox: [x0, y0, x1, y1] });
function house() {
  return { floors: [
    { id: "f1", label: "Floor 1", walls: [], openings: [], rooms: [
      room("K", "Kitchen", [0, 0, 4, 4]), room("L", "Living Room", [4, 0, 10, 4]), room("S1", "Staircase", [10, 0, 12, 4]),
    ] },
    { id: "f2", label: "Floor 2", walls: [], openings: [{ id: "d1", type: "doorway", center: [10, 3], angleDeg: 90, width: 0.8 }], rooms: [
      room("B1", "Bedroom", [0, 0, 4, 4]), room("B2", "Bedroom", [4, 0, 9, 4]), room("H", "Hallway", [9, 0, 10, 4]),
      room("S2", "Staircase", [10, 0, 12, 2]), room("W", "Bathroom", [10, 2, 12, 4]),
      room("U", "Bedroom", [0, 4, 4, 6], "unreliable"), room("V", "Void", [4, 4, 5, 5]),
    ] },
  ] };
}
const rot = (p, deg) => { const r = (deg * Math.PI) / 180, c = Math.round(Math.cos(r)), s = Math.round(Math.sin(r)); return [c * p[0] - s * p[1], s * p[0] + c * p[1]]; };
function turned(deg) {
  const h = house();
  for (const f of h.floors) {
    for (const r of f.rooms) { r.polygon = r.polygon.map((p) => rot(p, deg)); const xs = r.polygon.map((p) => p[0]), ys = r.polygon.map((p) => p[1]); r.bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]; }
    for (const o of f.openings) { o.center = rot(o.center, deg); o.angleDeg = (o.angleDeg + deg) % 360; }
  }
  return h;
}
const rel = (plan) => Object.fromEntries(plan.floors.flatMap((f) => f.rooms.filter((r) => r.type !== "Void").map((r) => [r.id,
  { size_rank: r.size_rank, size_of: r.size_of, next_to: r.next_to, over: r.over, under: r.under, ...("at_stairs" in r ? { at_stairs: r.at_stairs } : {}) }])));

const base = rel(withPlanExtras(house()));
const expect = {
  K: { size_rank: 1, size_of: 1, next_to: ["L"], over: [], under: ["B1"], at_stairs: false },
  L: { size_rank: 1, size_of: 1, next_to: ["K", "S1"], over: [], under: ["B2", "H"], at_stairs: true },
  S1: { size_rank: 1, size_of: 1, next_to: ["L"], over: [], under: ["S2", "W"] },
  B1: { size_rank: 2, size_of: 3, next_to: ["B2"], over: ["K"], under: [], at_stairs: false },
  B2: { size_rank: 1, size_of: 3, next_to: ["B1", "H"], over: ["L"], under: [], at_stairs: false },
  H: { size_rank: 1, size_of: 1, next_to: ["B2", "S2", "W"], over: ["L"], under: [], at_stairs: true },
  S2: { size_rank: 1, size_of: 1, next_to: ["H", "W"], over: ["S1"], under: [] },
  W: { size_rank: 1, size_of: 1, next_to: ["H", "S2"], over: ["S1"], under: [], at_stairs: true },
  U: { size_rank: 3, size_of: 3, next_to: [], over: [], under: [], at_stairs: false },
};
for (const [id, e] of Object.entries(expect)) ok(eq(base[id], e), `${id}: got ${JSON.stringify(base[id])}, expected ${JSON.stringify(e)}`);
ok(!("V" in base), "Void rooms get no relations");
// symmetry
for (const [id, r] of Object.entries(base)) {
  for (const x of r.over) ok(base[x].under.includes(id), `symmetry: ${id} over ${x} but ${x} not under ${id}`);
  for (const x of r.under) ok(base[x].over.includes(id), `symmetry: ${id} under ${x} but ${x} not over ${id}`);
  for (const x of r.next_to) ok(base[x].next_to.includes(id), `symmetry: ${id} next to ${x} but not the reverse`);
  ok(![...r.next_to, ...r.over, ...r.under].includes("U"), `unreliable room U appears in ${id}'s relations`);
}
// rotations
for (const deg of [90, 180, 270]) ok(eq(rel(withPlanExtras(turned(deg))), base), `relations change when the house is turned ${deg} degrees`);
// size tie: two bedrooms within 5 % share rank 1
const tie = house(); tie.floors[1].rooms[1] = room("B2", "Bedroom", [4, 0, 7.9, 4]); // 15.6 m2 vs 16 m2 (2.5 %)
const tr = rel(withPlanExtras(tie));
ok(tr.B1.size_rank === 1 && tr.B2.size_rank === 1, `size tie within 5 %: ${tr.B1.size_rank}, ${tr.B2.size_rank}`);

console.log(fail ? `${fail} FAILED` : "OK room relations (expected values, symmetry, 90/180/270 rotations, unreliable outline, size ties)");
process.exit(fail ? 1 : 0);
