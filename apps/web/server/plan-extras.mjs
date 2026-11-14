// Extras of a floor plan that Matterport does not provide, computed ONCE with the viewer's own logic and then read by
// everyone (plan index builder, viewer, planner), never recomputed:
//   plan.stairs[]          one entry per stair flight paired between consecutive floors (public/house-viewer/escaleras.js)
//                          { id: "stairs-<lower staircase room id>", lower_floor_id, upper_floor_id, lower_room_id,
//                            upper_room_id, state: ok | no_cabe | dudosa, footprint_scale, direction: 1 | -1 | null,
//                            direction_confidence: clara | estimada | null }
//   opening.exterior       true if one side (0.3 m away) is a room and the other is outside every room (evac.js rule),
//                          for every opening type and floor
//   room size and spatial relations (plan index v2; no orientation, no sides: there is no north and no street):
//     area_m2       room area from the API (m2)
//     size_rank     rank by area among the rooms of the same type on its floor (1 = largest; within 5 % share a rank)
//     size_of       number of rooms of that type on that floor
//     next_to       rooms of the same floor sharing a wall of at least NEXT_MIN_M (mutual)
//     over / under  rooms of the floor directly below / above overlapping it by at least OVER_MIN of the smaller area
//     at_stairs     shares a wall or an opening with a Staircase room of its floor (not set on Staircase rooms)
//   Rooms whose outline is not reliable (polygonQuality != ok) get empty lists and never appear in other rooms' lists.
// The ids are stable because Matterport room ids are stable.
import { analizar } from "../public/house-viewer/escaleras.js";
import { aperturaExterior, contextoPlanta, dentroSala, esEscalera, LADO } from "../public/house-viewer/evac.js";

const SIZE_TIE = 0.05;      // areas within 5 % of the first room of a rank share that rank
const NEXT_MIN_M = 0.5;     // shared wall length (m) to be next to each other
const NEXT_TOL_M = 0.3;     // a boundary point within this distance of the other outline is on the shared wall
const EDGE_STEP_M = 0.25;   // boundary sampling step (m)
const OVER_MIN = 0.3;       // overlap with the floor below, as a share of the smaller room's area
const GRID_M = 0.5;         // area sampling grid (m); points at cell centres, so 90/180/270 degree turns map it onto itself

const reliable = (r) => r.type !== "Void" && !!r.polygon && (r.polygonQuality || "ok") === "ok";
function distSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy, t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
const distRing = (p, ring) => Math.min(...ring.map((a, i) => distSeg(p, a, ring[(i + 1) % ring.length])));
// shared wall length from A's side: boundary samples of A within NEXT_TOL_M of B's outline
function sharedFrom(A, B) {
  let n = 0;
  A.polygon.forEach((a, i) => {
    const b = A.polygon[(i + 1) % A.polygon.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]), k = Math.max(1, Math.round(L / EDGE_STEP_M));
    for (let j = 0; j < k; j++) { const t = (j + 0.5) / k, p = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]; if (distRing(p, B.polygon) <= NEXT_TOL_M) n += L / k; }
  });
  return n;
}
// grid cells (centres) inside a room: Set of "i,j"
function cells(r) {
  const xs = r.polygon.map((p) => p[0]), ys = r.polygon.map((p) => p[1]), out = new Set();
  for (let i = Math.floor(Math.min(...xs) / GRID_M); i <= Math.ceil(Math.max(...xs) / GRID_M); i++)
    for (let j = Math.floor(Math.min(...ys) / GRID_M); j <= Math.ceil(Math.max(...ys) / GRID_M); j++)
      if (dentroSala([(i + 0.5) * GRID_M, (j + 0.5) * GRID_M], r)) out.add(`${i},${j}`);
  return out;
}
// opening whose two sides (LADO m away) fall in rooms A and B
function openingBetween(f, A, B) {
  return f.openings.some((o) => {
    const a = (o.angleDeg * Math.PI) / 180, n = [-Math.sin(a), Math.cos(a)];
    const p1 = [o.center[0] + LADO * n[0], o.center[1] + LADO * n[1]], p2 = [o.center[0] - LADO * n[0], o.center[1] - LADO * n[1]];
    return (dentroSala(p1, A) && dentroSala(p2, B)) || (dentroSala(p1, B) && dentroSala(p2, A));
  });
}

/** Adds area_m2, size_rank, size_of, next_to, over, under and at_stairs to every room of the plan (in place). */
export function addRoomRelations(plan) {
  const byFloor = plan.floors.map((f) => f.rooms.filter((r) => r.type !== "Void"));
  plan.floors.forEach((f, fi) => {
    const rooms = byFloor[fi];
    for (const r of rooms) Object.assign(r, { area_m2: r.areaM2 ?? null, size_rank: null, size_of: 0, next_to: [], over: [], under: [] });
    // size rank per type (competition ranking: the smallest has rank == size_of when there are no ties)
    const types = new Map();
    for (const r of rooms) { const k = (r.type || "").toLowerCase(); types.set(k, [...(types.get(k) || []), r]); }
    for (const group of types.values()) {
      const sized = group.filter((r) => typeof r.areaM2 === "number").sort((a, b) => b.areaM2 - a.areaM2);
      let leader = null, rank = 0;
      sized.forEach((r, i) => { if (!leader || r.areaM2 < leader.areaM2 * (1 - SIZE_TIE)) { leader = r; rank = i + 1; } r.size_rank = rank; });
      for (const r of group) r.size_of = group.length;
    }
    // next_to (mutual): mean of the shared length seen from each side
    const ok = rooms.filter(reliable);
    for (let i = 0; i < ok.length; i++) for (let j = i + 1; j < ok.length; j++) {
      const A = ok[i], B = ok[j];
      if ((sharedFrom(A, B) + sharedFrom(B, A)) / 2 >= NEXT_MIN_M) { A.next_to.push(B.id); B.next_to.push(A.id); }
    }
  });
  // over / under: once per pair (upper room, room of the floor directly below), so they are inverse by construction
  for (let fi = 1; fi < plan.floors.length; fi++) {
    const up = byFloor[fi].filter(reliable).map((r) => ({ r, c: cells(r) })), down = byFloor[fi - 1].filter(reliable).map((r) => ({ r, c: cells(r) }));
    for (const U of up) for (const D of down) {
      const small = Math.min(U.c.size, D.c.size);
      if (!small) continue;
      let inter = 0;
      for (const k of U.c) if (D.c.has(k)) inter++;
      if (inter / small >= OVER_MIN) { U.r.over.push(D.r.id); D.r.under.push(U.r.id); }
    }
  }
  // at_stairs: wall (next_to) or opening shared with a Staircase room of the same floor; not set on Staircase rooms
  plan.floors.forEach((f, fi) => {
    const stairsRooms = byFloor[fi].filter((r) => esEscalera(r) && reliable(r));
    for (const r of byFloor[fi]) {
      if (esEscalera(r)) { delete r.at_stairs; continue; }
      r.at_stairs = reliable(r) && stairsRooms.some((s) => r.next_to.includes(s.id) || openingBetween(f, r, s));
    }
  });
  for (const rooms of byFloor) for (const r of rooms) { r.next_to.sort(); r.over.sort(); r.under.sort(); }
  return plan;
}

/** Returns a copy of the plan with stairs[], an exterior flag on every opening, and room size and spatial relations. */
export function withPlanExtras(plan) {
  const out = structuredClone(plan);
  out.floors.forEach((f) => {
    const ctx = contextoPlanta(f);
    f.openings.forEach((o) => { o.exterior = aperturaExterior(f, o, ctx); });
  });
  out.stairs = analizar(plan).pares.map((E) => ({
    id: `stairs-${E.r.id}`,
    lower_floor_id: plan.floors[E.fi].id, upper_floor_id: plan.floors[E.fi + 1].id,
    lower_room_id: E.r.id, upper_room_id: E.q.id,
    state: E.estado, footprint_scale: E.escala ?? null, direction: E.dir ?? null, direction_confidence: E.conf ?? null,
  }));
  return addRoomRelations(out);
}
