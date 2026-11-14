// Deterministic route planner for incident objectives (docs/INCIDENT_OVERLAY_SPEC.md, "Routes").
// Graph: rooms and their connections from the evacuation graph (evac.js: doorways and invisible walls between rooms);
// between floors ONLY the stairs stored in plan.json (plan.stairs[]). Entries: exterior doors and windows of the ground
// floor. Cost = length + a penalty near reported fire or smoke (never a block); passes_near_fire marks it.
// rescue: entry -> target (occupant room, room or floor); recon: entry -> room or floor. Occupant groups (contradicted
// candidates): one route per candidate. No path -> no route (the viewer shows "No route computable").
import { evacuacion, dentroSala, FLOOR_H, LADO } from '../evac.js'

const WINDOW_PENALTY = 2      // m: climbing in through a window costs a bit more than a door
const NEAR_FIRE_M = 3         // m: an edge this close to a fire point counts as near fire
const PENALTY = { fire: 30, heat: 30, smoke: 12 } // m added to an edge in or near a fire room
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1])
function distSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy
  const t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)
}

/** Floor index of a location (room, anchor or floor), or null. */
export function floorOf(plan, loc) {
  if (!loc) return null
  const roomId = loc.kind === 'room' ? loc.room_id : loc.kind === 'anchor' ? loc.anchor?.room_id : null
  if (roomId) { const fi = plan.floors.findIndex((f) => f.rooms.some((r) => r.id === roomId)); return fi >= 0 ? fi : null }
  if (loc.kind === 'floor') {
    if (loc.floor_id) { const fi = plan.floors.findIndex((f) => f.id === loc.floor_id); return fi >= 0 ? fi : null }
    if (Number.isInteger(loc.floor_reported) && loc.floor_reported >= 1 && loc.floor_reported <= plan.floors.length) return loc.floor_reported - 1
  }
  return null
}
/** Point inside the room for an anchor (near a window, door or stairs, else its center), or null. */
export function anchorPoint(plan, ev, loc) {
  const a = loc.anchor || {}, fi = floorOf(plan, loc), node = fi === null ? null : ev.nodos.get(ev.clave(fi, { id: a.room_id }))
  if (!node) return null
  const op = a.ref_id && plan.floors[fi].openings.find((o) => o.id === a.ref_id)
  if (op) { const t = 0.55; return [op.center[0] + (node.c[0] - op.center[0]) * Math.min(1, t / Math.max(0.01, dist(op.center, node.c))), op.center[1] + (node.c[1] - op.center[1]) * Math.min(1, t / Math.max(0.01, dist(op.center, node.c)))] }
  const st = a.ref_id && (plan.stairs || []).find((s) => s.id === a.ref_id)
  if (st) { const n = ev.nodos.get(ev.clave(fi, { id: fi === plan.floors.findIndex((f) => f.id === st.lower_floor_id) ? st.lower_room_id : st.upper_room_id })); if (n) return n.c }
  return node.c // center (the contract has no orientation: no cardinal sides)
}

export function planRoutes(plan, overlay, ev = evacuacion(plan)) {
  const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now()
  const key = (fi, roomId) => ev.clave(fi, { id: roomId })
  // fire points per floor (room or anchor level only; floor-only fire has no point)
  const fires = []
  for (const f of overlay.fire || []) {
    const fi = floorOf(plan, f.location); if (fi === null || f.location.kind === 'floor') continue
    const roomId = f.location.kind === 'room' ? f.location.room_id : f.location.anchor.room_id
    const node = ev.nodos.get(key(fi, roomId)); if (!node) continue
    fires.push({ fi, roomKey: node.key, p: f.location.kind === 'anchor' ? anchorPoint(plan, ev, f.location) || node.c : node.c, pen: PENALTY[f.kind] ?? 12 })
  }
  const fireOn = (a, b, pts, fi) => {
    let pen = 0
    for (const F of fires) {
      if (F.fi !== fi) continue
      if (F.roomKey === a || F.roomKey === b) pen = Math.max(pen, F.pen)
      else if (pts.slice(1).some((p, i) => distSeg(F.p, pts[i], p) < NEAR_FIRE_M)) pen = Math.max(pen, F.pen / 2)
    }
    return pen
  }
  // adjacency: room-room edges of the evacuation graph (no evac stairs) + stairs from plan.json
  const ady = new Map([...ev.nodos.keys()].map((k) => [k, []]))
  const add = (a, b, e) => { ady.get(a).push({ ...e, to: b }); ady.get(b).push({ ...e, to: a }) }
  for (const e of ev.aristas) {
    if (e.tipo === 'stairs') continue
    const A = ev.nodos.get(e.a), B = ev.nodos.get(e.b), len = dist(A.c, e.punto) + dist(e.punto, B.c), pen = fireOn(e.a, e.b, [A.c, e.punto, B.c], A.fi)
    add(e.a, e.b, { len, cost: len + pen, near: pen > 0, via: e.tipo === 'doorway' ? { kind: 'opening', ref_id: e.id, p: e.punto } : { p: e.punto } })
  }
  for (const s of plan.stairs || []) {
    const fl = plan.floors.findIndex((f) => f.id === s.lower_floor_id), a = key(fl, s.lower_room_id), b = key(fl + 1, s.upper_room_id)
    if (!ev.nodos.has(a) || !ev.nodos.has(b)) continue
    const len = Math.hypot(dist(ev.nodos.get(a).c, ev.nodos.get(b).c), FLOOR_H), pen = Math.max(fireOn(a, a, [ev.nodos.get(a).c], fl), fireOn(b, b, [ev.nodos.get(b).c], fl + 1))
    add(a, b, { len, cost: len + pen, near: pen > 0, via: { kind: 'stairs', ref_id: s.id } })
  }
  // entries: exterior doors and windows of the ground floor, into the room on their inner side
  const entries = []
  for (const o of plan.floors[0]?.openings || []) {
    if (!o.exterior || (o.type !== 'doorway' && o.type !== 'window')) continue
    const a = (o.angleDeg * Math.PI) / 180, n = [-Math.sin(a), Math.cos(a)]
    const node = [1, -1].map((s) => [o.center[0] + s * LADO * n[0], o.center[1] + s * LADO * n[1]])
      .map((p) => [...ev.nodos.values()].filter((x) => x.fi === 0).sort((x, y) => (x.room.areaM2 || 0) - (y.room.areaM2 || 0)).find((x) => dentroSala(p, x.room))).find(Boolean)
    if (!node) continue
    const len = dist(o.center, node.c), pen = fireOn(node.key, node.key, [o.center, node.c], 0)
    entries.push({ to: node.key, len, cost: len + pen + (o.type === 'window' ? WINDOW_PENALTY : 0), near: pen > 0, opening: o })
  }
  // Dijkstra from a virtual outside node; target: a room key or any room on a floor
  function route(target) {
    const d = new Map(), pred = new Map(), done = new Set()
    for (const e of entries) if (!d.has(e.to) || e.cost < d.get(e.to)) { d.set(e.to, e.cost); pred.set(e.to, { entry: e }) }
    let hit = null
    while (true) {
      let u = null
      for (const [k, v] of d) if (!done.has(k) && (u === null || v < d.get(u))) u = k
      if (u === null) break
      done.add(u)
      if (target.room ? u === target.room : ev.nodos.get(u).fi === target.floor) { hit = u; break }
      for (const e of ady.get(u)) { const nd = d.get(u) + e.cost; if (!d.has(e.to) || nd < d.get(e.to) - 1e-9) { d.set(e.to, nd); pred.set(e.to, { from: u, e }) } }
    }
    if (!hit) return null
    const steps = []
    for (let k = hit, g = 0; g < 1000; g++) { const p = pred.get(k); steps.unshift({ k, ...p }); if (p.entry) break; k = p.from }
    const first = steps[0].entry, waypoints = [{ kind: 'exterior', ref_id: first.opening.id }], geom = [{ p: first.opening.center, fi: 0 }]
    let len = first.len, near = first.near
    geom.push({ p: ev.nodos.get(steps[0].k).c, fi: 0, near: first.near })
    waypoints.push({ kind: 'room', ref_id: ev.nodos.get(steps[0].k).room.id })
    for (const s of steps.slice(1)) {
      const to = ev.nodos.get(s.k), from = ev.nodos.get(s.from)
      if (s.e.via.kind) waypoints.push({ kind: s.e.via.kind, ref_id: s.e.via.ref_id })
      if (s.e.via.p) geom.push({ p: s.e.via.p, fi: from.fi, near: s.e.near })
      else geom.push({ p: from.c, fi: to.fi, near: s.e.near, stairs: true })
      geom.push({ p: to.c, fi: to.fi, near: s.e.near })
      waypoints.push({ kind: 'room', ref_id: to.room.id })
      len += s.e.len; near = near || s.e.near
    }
    return { waypoints, geom, length: Math.round(len * 10) / 10, near }
  }
  // objectives -> targets
  const occ = new Map((overlay.occupants || []).map((o) => [o.id, o]))
  const targetOf = (loc) => {
    const fi = floorOf(plan, loc)
    if (fi === null) return null
    if (loc.kind === 'floor') return { floor: fi }
    const roomId = loc.kind === 'room' ? loc.room_id : loc.anchor.room_id
    return ev.nodos.has(key(fi, roomId)) ? { room: key(fi, roomId) } : null
  }
  const routes = [], geometry = new Map(), noRoute = []
  for (const o of [...(overlay.objectives || [])].sort((a, b) => a.priority - b.priority)) {
    const t = o.target || {}
    let cands = []
    if (t.kind === 'occupant') cands = [{ suffix: '', target: occ.has(t.ref_id) ? targetOf(occ.get(t.ref_id).location) : null }]
    else if (t.kind === 'occupant_group') cands = [...occ.values()].filter((x) => x.group_id === t.ref_id).map((x) => ({ suffix: '-' + x.id, target: targetOf(x.location), candidate: x.id }))
    else if (t.kind === 'room') { const fi = plan.floors.findIndex((f) => f.rooms.some((r) => r.id === t.ref_id)); cands = [{ suffix: '', target: fi >= 0 && ev.nodos.has(key(fi, t.ref_id)) ? { room: key(fi, t.ref_id) } : null }] }
    else if (t.kind === 'floor') cands = [{ suffix: '', target: targetOf({ kind: 'floor', floor_id: t.ref_id, floor_reported: t.floor_reported }) }]
    let any = false
    for (const c of cands) {
      const r = c.target && route(c.target)
      if (!r) continue
      any = true
      const id = `route-${o.id}${c.suffix}`
      routes.push({ id, objective_id: o.id, produced_by: 'planner', kind: o.type, status: 'inferred',
        confidence: c.candidate ? 0.4 : c.target.floor !== undefined ? 0.4 : 0.6, waypoints: r.waypoints, length_m_estimate: r.length, passes_near_fire: r.near,
        note: 'Planner estimate over the scanned plan, straight lines between room and door centers' + (c.candidate ? '; one of several contradicted candidates' : '') + (c.target.floor !== undefined ? '; to the floor, room unknown' : '') })
      geometry.set(id, { pts: r.geom, dashed: !!c.candidate })
    }
    if (!any) noRoute.push(o.id)
  }
  const ms = Math.round(((typeof performance !== 'undefined' ? performance.now() : Date.now()) - t0) * 10) / 10
  return { routes, geometry, noRoute, ms }
}
