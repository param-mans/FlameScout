// Plan index of the loaded plan (docs/INCIDENT_OVERLAY_SPEC.md): what the incident agent uses to resolve phrases into ids.
// Same rules and key order as scripts/make_plan_index.py (viewer layout); a test checks both give identical output.
// Reads stairs[] and opening.exterior stored in the plan (added once by the server, apps/web/server/plan-extras.mjs).
//   { version: 2, floors: [floor ids], rooms: { id: { floor_id, label, type, area_m2, size_rank, size_of, next_to, over, under, at_stairs? } },
//     openings: { id: { type, exterior, floor_id } },
//     stairs: [stair ids] }      Void rooms are left out.

export const emptyPlanIndex = () => ({ floors: [], rooms: {}, openings: {}, stairs: [] })
// room size and relations stored in the plan (plan-extras.mjs); copied, never recomputed. at_stairs is absent on staircases.
const REL = ['area_m2', 'size_rank', 'size_of', 'next_to', 'over', 'under']

export function buildPlanIndex(plan) {
  if (!Array.isArray(plan.stairs) || plan.stairs.some((s) => !s || !s.id)) throw new Error('plan has no stairs[] with an id per flight')
  const rooms = {}, openings = {}
  for (const f of plan.floors) {
    for (const r of f.rooms || []) {
      if (r.type === 'Void') continue
      if (!REL.every((k) => k in r)) throw new Error(`room ${r.id} has no stored size / relations`)
      rooms[r.id] = { floor_id: f.id, label: String(r.label || r.type || 'Room').toUpperCase(), type: r.type ? String(r.type).toLowerCase() : null }
      for (const k of REL) rooms[r.id][k] = Array.isArray(r[k]) ? [...r[k]] : r[k]
      if ('at_stairs' in r) rooms[r.id].at_stairs = r.at_stairs
    }
    for (const o of f.openings || []) {
      if (typeof o.exterior !== 'boolean') throw new Error(`opening ${o.id} has no exterior flag`)
      openings[o.id] = { type: o.type, exterior: o.exterior, floor_id: f.id }
    }
  }
  return { version: 2, floors: plan.floors.map((f) => f.id), rooms, openings, stairs: plan.stairs.map((s) => s.id) }
}
