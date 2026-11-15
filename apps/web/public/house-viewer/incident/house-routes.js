// House screen (layout=house) of the MDT: people figures and the two incident routes, computed from the validated overlay
// over the same incident graph as the planner (planner.js incidentGraph: evacuation graph edges, plan.json stairs only
// between floors, fire penalty on edges near a reported fire, never a block). Pure module (no DOM).
//   housePeople(plan, ev, overlay) -> [{ fi, roomId, key, p, n, color: 'green' | 'yellow', id }]
//     green: occupant in one room (room or anchor), not contradicted. yellow: each candidate of a contradicted group (or a
//     contradicted occupant), and floor-level occupants (in the floor's at_stairs room, else its largest room).
//     n = figures for that entity (count, or the top of count_range), 5 at most; 1 for a floor-level report.
//   houseRoutes(plan, ev, overlay, people) -> { red, green, entrance, unreachable }
//     red: entrance -> room of the highest-confidence fire (length only). green: entrance -> every person (shortest order:
//     all permutations up to 8 stops, nearest neighbour above) -> nearest exterior door, with the fire penalty.
//     entrance: the ground-floor exterior door that minimizes the green route (the red one when nobody is reported).
//     Each route: { pts: [{ p, fi, near?, stairs? }] } (same geometry as planRoutes). unreachable: stops not drawn.
import { evacuacion, poligonoValido, dibujable, puntoInterior } from '../evac.js'
import { incidentGraph, floorOf, anchorPoint } from './planner.js'

export const MAX_FIGURES = 5
const MAX_PERM = 8
const count = (o) => (o.count_range ? o.count_range[1] : o.count) || 1

/** Room used for a floor-level report: the at_stairs room of that floor (the largest if several), else the largest room. */
export function floorRoom(plan, ev, fi) {
  const rooms = plan.floors[fi].rooms.filter((r) => dibujable(r) && poligonoValido(r) && ev.nodos.has(ev.clave(fi, r)))
  const big = (l) => l.slice().sort((a, b) => (b.areaM2 || 0) - (a.areaM2 || 0))[0] || null
  return big(rooms.filter((r) => r.at_stairs === true)) || big(rooms)
}

export function housePeople(plan, ev, overlay) {
  const out = []
  for (const o of overlay?.occupants || []) {
    const loc = o.location, fi = floorOf(plan, loc)
    if (fi === null) continue
    const n = loc.kind === 'floor' ? 1 : Math.min(MAX_FIGURES, count(o))   // floor level: one yellow figure
    let room, p
    if (loc.kind === 'floor') { room = floorRoom(plan, ev, fi); if (!room) continue; p = ev.nodos.get(ev.clave(fi, room)).c }
    else {
      room = plan.floors[fi].rooms.find((r) => r.id === (loc.kind === 'room' ? loc.room_id : loc.anchor.room_id))
      if (!room || !dibujable(room)) continue
      const node = ev.nodos.get(ev.clave(fi, room))
      p = loc.kind === 'anchor' ? anchorPoint(plan, ev, loc) : null
      p = p || (node ? node.c : poligonoValido(room) ? puntoInterior(room) : room.bbox ? [(room.bbox[0] + room.bbox[2]) / 2, (room.bbox[1] + room.bbox[3]) / 2] : null)
      if (!p) continue
    }
    const yellow = loc.kind === 'floor' || o.status === 'contradicted'
    out.push({ id: o.id, fi, roomId: room.id, key: ev.nodos.has(ev.clave(fi, room)) ? ev.clave(fi, room) : null, p, n, color: yellow ? 'yellow' : 'green' })
  }
  return out
}

function dijkstra(ady, sources, useLen) {
  const d = new Map(), pred = new Map(), done = new Set()
  for (const [k, c] of sources) if (!d.has(k) || c < d.get(k)) { d.set(k, c); pred.set(k, null) }
  for (;;) {
    let u = null
    for (const [k, v] of d) if (!done.has(k) && (u === null || v < d.get(u))) u = k
    if (u === null) break
    done.add(u)
    for (const e of ady.get(u) || []) {
      const nd = d.get(u) + (useLen ? e.len : e.cost)
      if (!d.has(e.to) || nd < d.get(e.to) - 1e-9) { d.set(e.to, nd); pred.set(e.to, { from: u, e }) }
    }
  }
  return { d, pred }
}
// geometry of the path from the Dijkstra source to `to` (first point excluded: the source node's center)
function legGeom(ev, pred, to) {
  const steps = []
  for (let k = to, g = 0; g < 1000; g++) { const p = pred.get(k); if (!p) break; steps.unshift({ k, ...p }); k = p.from }
  const out = []
  for (const s of steps) {
    const A = ev.nodos.get(s.from), B = ev.nodos.get(s.k)
    if (s.e.via.p) out.push({ p: s.e.via.p, fi: A.fi, near: s.e.near })
    else out.push({ p: A.c, fi: B.fi, near: s.e.near, stairs: true })
    out.push({ p: B.c, fi: B.fi, near: s.e.near })
  }
  return out
}

export function houseRoutes(plan, ev = evacuacion(plan), overlay, people = housePeople(plan, ev, overlay)) {
  const { ady, entries: all } = incidentGraph(plan, overlay, ev)
  const doors = all.filter((e) => e.opening.type === 'doorway')
  // fire target: the highest-confidence fire, smoke or heat report with a room
  let fireKey = null, best = -1
  for (const f of overlay?.fire || []) {
    const fi = floorOf(plan, f.location)
    if (fi === null || f.location.kind === 'floor' || f.location.kind === 'unknown') continue
    const k = ev.clave(fi, { id: f.location.kind === 'room' ? f.location.room_id : f.location.anchor.room_id })
    if (ev.nodos.has(k) && f.confidence > best) { best = f.confidence; fireKey = k }
  }
  const stops = [...new Set(people.map((x) => x.key).filter(Boolean))]
  const lost = people.filter((x) => !x.key).length // a person in a room outside the graph: never reachable
  const fromStop = new Map(stops.map((k) => [k, dijkstra(ady, [[k, 0]], false)]))
  const fireD = fireKey ? dijkstra(ady, [[fireKey, 0]], true) : null
  const viaDoor = (D, e, len) => (D.d.has(e.to) ? D.d.get(e.to) + (len ? e.len : e.cost) : Infinity)
  const exitCost = (k) => Math.min(Infinity, ...doors.map((e) => viaDoor(fromStop.get(k), e, false)))

  // best order of the reachable stops from one door: [cost, order]
  function order(door) {
    const reach = stops.filter((k) => Number.isFinite(viaDoor(fromStop.get(k), door, false)))
    const start = (k) => viaDoor(fromStop.get(k), door, false), pair = (a, b) => fromStop.get(a).d.get(b)
    if (!reach.length) return { cost: 0, seq: [], reach }
    if (reach.length <= MAX_PERM) {
      let bestC = Infinity, bestS = null
      const used = new Set(), seq = []
      const go = (prev, c) => {
        if (c >= bestC) return
        if (seq.length === reach.length) { const t = c + exitCost(prev); if (t < bestC) { bestC = t; bestS = seq.slice() } return }
        for (const k of reach) if (!used.has(k)) { used.add(k); seq.push(k); go(k, c + (prev === null ? start(k) : pair(prev, k))); seq.pop(); used.delete(k) }
      }
      go(null, 0)
      return { cost: bestC, seq: bestS, reach }
    }
    const seq = [], left = new Set(reach)
    let prev = null, c = 0
    while (left.size) {
      let nk = null, nc = Infinity
      for (const k of left) { const x = prev === null ? start(k) : pair(prev, k); if (x < nc) { nc = x; nk = k } }
      seq.push(nk); left.delete(nk); c += nc; prev = nk
    }
    return { cost: c + exitCost(prev), seq, reach }
  }
  // entrance: most stops reached, then the shortest green route; nobody reported: the shortest red route
  let entrance = null, plan_ = null
  for (const door of doors) {
    const o = order(door), red = fireD ? viaDoor(fireD, door, true) : Infinity
    const score = stops.length ? [-o.reach.length, o.cost] : [Number.isFinite(red) ? 0 : 1, red]
    if (!entrance || score[0] < plan_.score[0] || (score[0] === plan_.score[0] && score[1] < plan_.score[1])) { entrance = door; plan_ = { score, o } }
  }
  const res = { red: null, green: null, entrance: entrance ? entrance.opening.id : null, unreachable: 0 }
  if (!entrance) { res.unreachable = stops.length + lost + (fireKey ? 1 : 0); return res }
  const head = (e) => [{ p: e.opening.center, fi: 0 }, { p: ev.nodos.get(e.to).c, fi: 0, near: e.near }]
  // red: length only (it goes to the fire)
  if (fireKey) {
    const D = dijkstra(ady, [[entrance.to, 0]], true)
    if (D.d.has(fireKey)) res.red = { pts: [...head(entrance), ...legGeom(ev, D.pred, fireKey)] }
    else res.unreachable++
  }
  const { seq, reach } = plan_.o
  res.unreachable += stops.length - reach.length + lost
  if (seq && seq.length) {
    const pts = head(entrance)
    let cur = entrance.to
    for (const k of seq) { pts.push(...legGeom(ev, dijkstra(ady, [[cur, 0]], false).pred, k)); cur = k }
    const D = dijkstra(ady, [[cur, 0]], false)
    const exit = doors.filter((e) => D.d.has(e.to)).sort((a, b) => viaDoor(D, a, false) - viaDoor(D, b, false))[0]
    if (exit) { pts.push(...legGeom(ev, D.pred, exit.to), { p: exit.opening.center, fi: 0, near: exit.near }) }
    res.green = { pts }
  }
  return res
}
