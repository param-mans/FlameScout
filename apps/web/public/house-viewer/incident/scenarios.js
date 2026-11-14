// Debug scenarios (?debug=1): valid IncidentOverlays built from the REAL ids of the loaded house (its plan index and plan).
// Nothing is invented: if a house lacks what a scenario needs (e.g. no kitchen), the scenario is not offered.
// Evidence ids are fictitious call spans. Wording is advisory ("Consider ..."). Routes are always [] (the planner adds them).
//   S1  one caller: smoke in the kitchen, occupants in a bedroom
//   S2  two callers contradict each other about the bedroom (v1, v2)
//   S3  floor-only locations
//   S4  Fairdale (209217448): neighbour call (v1), mother's call contradicting it (v2)
//   S5  building without a scan: floor-only and unknown locations, no routes
const T0 = Date.parse('2026-11-15T03:00:00Z')
const iso = (min) => new Date(T0 + min * 60000).toISOString().replace('.000Z', 'Z')
const ev = (call, a, b) => `call-${call}:${a.toFixed(1)}-${b.toFixed(1)}`

function rooms(index, type, floorIdx) {
  const fid = floorIdx === undefined ? null : index.floors[floorIdx]
  return Object.entries(index.rooms).filter(([, r]) => r.type === type && (fid === null || r.floor_id === fid)).map(([id]) => id)
}
// windows of a room: window openings within 0.3 m of the room outline (plan geometry), ids checked against the index
function windowsOf(plan, index, roomId) {
  for (const f of plan.floors) {
    const r = f.rooms.find((x) => x.id === roomId)
    if (!r || !r.polygon) continue
    const d = (p) => Math.min(...r.polygon.map((a, i) => { const b = r.polygon[(i + 1) % r.polygon.length], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy, t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0; return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy) }))
    return f.openings.filter((o) => o.type === 'window' && index.openings[o.id] && d(o.center) <= 0.3).map((o) => o.id)
  }
  return []
}
const base = (id, modelId, version, asOf, changes) => ({ incident_id: id, model_id: modelId, version, as_of: iso(asOf), changes_since_last: changes,
  occupants: [], fire: [], hazards: [], blocked: [], contradictions: [], objectives: [], considerations: [], routes: [] })

/** [{ key, label, versions: [overlay, ...] }] available for this house. */
export function scenariosFor(sampleId, modelId, plan, index) {
  const out = []
  if (!plan) { // no interior scan
    const v = base('inc-s5', modelId, 1, 3, [])
    v.occupants.push({ id: 'occ-1', count_range: [1, 3], location: { kind: 'floor', floor_reported: 1 }, status: 'reported', confidence: 0.4, reported_at: iso(2), evidence_ids: [ev(1, 20, 27)], note: 'Caller thinks a few workers are still inside, no area given' })
    v.fire.push({ id: 'fire-1', kind: 'smoke', location: { kind: 'unknown' }, status: 'reported', confidence: 0.5, reported_at: iso(2), evidence_ids: [ev(1, 8, 14)] })
    v.objectives.push({ id: 'obj-1', type: 'recon', priority: 1, target: { kind: 'floor', floor_reported: 1 }, rationale: 'Occupants are reported inside and nothing is known about the interior', evidence_ids: [ev(1, 20, 27)] })
    v.considerations.push({ id: 'con-1', text: 'Consider confirming the number of people inside and where the smoke is coming from before committing to an interior plan', rationale: 'Both facts come from a single caller and no interior scan is on file for this building', confidence: 0.7, evidence_ids: [ev(1, 8, 14), ev(1, 20, 27)], refs: ['occ-1', 'fire-1'] })
    out.push({ key: 'S5', label: 'S5 · no scan', versions: [v] })
    return out
  }
  const nF = index.floors.length, top = nF - 1
  const kitchen = rooms(index, 'kitchen')[0]
  const bedsTop = rooms(index, 'bedroom', top), bedsAll = rooms(index, 'bedroom')
  const beds = bedsTop.length ? bedsTop : bedsAll
  if (kitchen && beds.length) {
    const v = base('inc-s1', modelId, 1, 4, [])
    v.fire.push({ id: 'fire-1', kind: 'smoke', location: { kind: 'room', room_id: kitchen }, status: 'reported', confidence: 0.8, reported_at: iso(1), evidence_ids: [ev(1, 6, 12)], note: 'Caller sees smoke in the kitchen' })
    v.occupants.push({ id: 'occ-1', count: 2, location: { kind: 'room', room_id: beds[0] }, status: 'reported', confidence: 0.6, reported_at: iso(2), evidence_ids: [ev(1, 30, 38)], note: 'Two people in a bedroom' })
    v.objectives.push({ id: 'obj-1', type: 'rescue', priority: 1, target: { kind: 'occupant', ref_id: 'occ-1' }, rationale: 'Two occupants reported in a bedroom', evidence_ids: [ev(1, 30, 38)] },
      { id: 'obj-2', type: 'recon', priority: 2, target: { kind: 'room', ref_id: kitchen }, rationale: 'Smoke reported in the kitchen', evidence_ids: [ev(1, 6, 12)] })
    v.considerations.push({ id: 'con-1', text: 'Consider confirming with the caller that both occupants are still in that bedroom', rationale: 'Only one caller reported them and the report is a few minutes old', confidence: 0.7, evidence_ids: [ev(1, 30, 38)], refs: ['occ-1'] })
    out.push({ key: 'S1', label: 'S1 · one caller', versions: [v] })
  }
  if (kitchen && beds.length >= 2) {
    const occ = (id, room, call, a, b, status, note, min) => ({ id, count: 2, location: { kind: 'room', room_id: room }, status, confidence: status === 'contradicted' ? 0.5 : 0.6, reported_at: iso(min), evidence_ids: [ev(call, a, b)], note })
    const smoke = { id: 'fire-1', kind: 'smoke', location: { kind: 'room', room_id: kitchen }, status: 'reported', confidence: 0.8, reported_at: iso(1), evidence_ids: [ev(1, 6, 12)] }
    const v1 = base('inc-s2', modelId, 1, 3, [])
    v1.fire.push(smoke); v1.occupants.push(occ('occ-1a', beds[0], 1, 40, 48, 'reported', 'Call 1: two children in a bedroom', 2))
    v1.objectives.push({ id: 'obj-1', type: 'rescue', priority: 1, target: { kind: 'occupant', ref_id: 'occ-1a' }, rationale: 'Two children reported in a bedroom', evidence_ids: [ev(1, 40, 48)] })
    v1.considerations.push({ id: 'con-1', text: 'Consider asking the caller which bedroom, if there is more than one on that floor', rationale: 'The bedroom was named by one caller only', confidence: 0.6, evidence_ids: [ev(1, 40, 48)], refs: ['occ-1a'] })
    const v2 = base('inc-s2', modelId, 2, 6, ['Call 2 reports the children in a different bedroom than Call 1'])
    v2.fire.push(smoke)
    v2.occupants.push({ ...occ('occ-1a', beds[0], 1, 40, 48, 'contradicted', 'Call 1: two children in a bedroom', 2), group_id: 'occ-loc-1' },
      { ...occ('occ-1b', beds[1], 2, 12, 19, 'contradicted', 'Call 2: two children in another bedroom', 5), group_id: 'occ-loc-1' })
    v2.contradictions.push({ group_id: 'occ-loc-1', key: 'occupants.location', resolved_by_dispatcher: false })
    v2.objectives.push({ id: 'obj-1', type: 'rescue', priority: 1, target: { kind: 'occupant_group', ref_id: 'occ-loc-1' }, rationale: 'Two children reported, the callers name different bedrooms', evidence_ids: [ev(1, 40, 48), ev(2, 12, 19)] })
    v2.considerations.push({ id: 'con-1', text: 'Consider asking both callers which bedroom the children are in before choosing a rescue approach', rationale: 'The two callers name different bedrooms and nobody has confirmed either', confidence: 0.8, evidence_ids: [ev(1, 40, 48), ev(2, 12, 19)], refs: ['occ-loc-1'] })
    out.push({ key: 'S2', label: 'S2 · contradicting callers', versions: [v1, v2] })
  }
  {
    const v = base('inc-s3', modelId, 1, 4, [])
    v.occupants.push({ id: 'occ-1', count_range: [1, 2], location: { kind: 'floor', floor_reported: nF }, status: 'reported', confidence: 0.4, reported_at: iso(2), evidence_ids: [ev(1, 22, 28)], note: `Someone heard on floor ${nF}, no room given` })
    v.fire.push({ id: 'fire-1', kind: 'smoke', location: { kind: 'floor', floor_reported: 1 }, status: 'reported', confidence: 0.6, reported_at: iso(1), evidence_ids: [ev(1, 5, 10)], note: 'Smoke on the ground floor, no room given' })
    v.objectives.push({ id: 'obj-1', type: 'recon', priority: 1, target: { kind: 'floor', floor_reported: nF }, rationale: `Occupants reported on floor ${nF}, room unknown`, evidence_ids: [ev(1, 22, 28)] })
    v.considerations.push({ id: 'con-1', text: 'Consider asking the caller for the room or the side of the building', rationale: 'Only the floor is known for both the occupants and the smoke', confidence: 0.7, evidence_ids: [ev(1, 22, 28), ev(1, 5, 10)], refs: ['occ-1', 'fire-1'] })
    out.push({ key: 'S3', label: 'S3 · floor only', versions: [v] })
  }
  if (sampleId === '209217448') {
    const office = rooms(index, 'office', 0)[0], garage = rooms(index, 'garage')[0], k = rooms(index, 'kitchen', 0)[0]
    const kWin = k ? windowsOf(plan, index, k)[0] : null
    if (office && garage && k && kWin && nF >= 2) {
      const smoke = { id: 'fire-1', kind: 'smoke', location: { kind: 'room', room_id: k }, status: 'reported', confidence: 0.8, reported_at: iso(1), evidence_ids: [ev(1, 4, 11)], note: 'Neighbour: smoke coming from the kitchen' }
      const kids = { id: 'occ-1a', count: 2, location: { kind: 'floor', floor_reported: 2 }, status: 'reported', confidence: 0.5, reported_at: iso(1), evidence_ids: [ev(1, 18, 26)], note: 'Neighbour: two children upstairs' }
      const v1 = base('inc-s4', modelId, 1, 2, [])
      v1.fire.push(smoke); v1.occupants.push(kids)
      v1.objectives.push({ id: 'obj-1', type: 'rescue', priority: 1, target: { kind: 'occupant', ref_id: 'occ-1a' }, rationale: 'Neighbour reports two children upstairs', evidence_ids: [ev(1, 18, 26)] },
        { id: 'obj-2', type: 'recon', priority: 2, target: { kind: 'room', ref_id: k }, rationale: 'Smoke reported from the kitchen', evidence_ids: [ev(1, 4, 11)] })
      v1.considerations.push({ id: 'con-1', text: 'Consider confirming where the children are with someone who lives in the house', rationale: 'The only report comes from a neighbour outside the house', confidence: 0.7, evidence_ids: [ev(1, 18, 26)], refs: ['occ-1a'] })
      const v2 = base('inc-s4', modelId, 2, 12, ['Call 2 (mother) places the two children in a bedroom downstairs, contradicting Call 1 (upstairs)',
        'Call 2 reports her husband in the office', 'Call 2 reports a possible gas smell near the garage', 'Call 2 reports flames at a kitchen window'])
      v2.fire.push(smoke, { id: 'fire-2', kind: 'fire', location: { kind: 'anchor', anchor: { room_id: k, position: 'near_window', ref_id: kWin } }, status: 'reported', confidence: 0.7, reported_at: iso(11), evidence_ids: [ev(2, 3, 9)], note: 'Mother: flames at the kitchen window' })
      v2.occupants.push({ ...kids, status: 'contradicted', group_id: 'occ-kids' },
        { id: 'occ-1b', count: 2, location: { kind: 'floor', floor_reported: 1 }, status: 'contradicted', confidence: 0.5, group_id: 'occ-kids', reported_at: iso(11), evidence_ids: [ev(2, 14, 22)], note: 'Mother: the two children are in a bedroom downstairs' },
        { id: 'occ-2', count: 1, location: { kind: 'room', room_id: office }, status: 'reported', confidence: 0.6, reported_at: iso(11), evidence_ids: [ev(2, 25, 31)], note: 'Mother: her husband is in the office' })
      v2.hazards.push({ id: 'haz-1', kind: 'gas', location: { kind: 'room', room_id: garage }, status: 'reported', confidence: 0.3, reported_at: iso(11), evidence_ids: [ev(2, 40, 44)], note: 'Mother: possible gas smell near the garage' })
      v2.contradictions.push({ group_id: 'occ-kids', key: 'occupants.location', resolved_by_dispatcher: false })
      v2.objectives.push({ id: 'obj-1', type: 'rescue', priority: 1, target: { kind: 'room', ref_id: office }, rationale: 'Husband reported in the office by the mother', evidence_ids: [ev(2, 25, 31)] },
        { id: 'obj-2', type: 'rescue', priority: 2, target: { kind: 'occupant_group', ref_id: 'occ-kids' }, rationale: 'Two children reported; the callers place them on different floors', evidence_ids: [ev(1, 18, 26), ev(2, 14, 22)] },
        { id: 'obj-3', type: 'recon', priority: 3, target: { kind: 'room', ref_id: garage }, rationale: 'Possible gas smell reported near the garage', evidence_ids: [ev(2, 40, 44)] })
      v2.considerations.push({ id: 'con-1', text: 'Consider asking the mother which bedroom downstairs the children are in', rationale: 'The neighbour said upstairs, the mother said downstairs; she is more likely to know, but nobody has confirmed either', confidence: 0.8, evidence_ids: [ev(1, 18, 26), ev(2, 14, 22)], refs: ['occ-kids'] },
        { id: 'con-2', text: 'Consider treating the kitchen and the routes next to it as exposed to fire', rationale: 'Smoke was reported in the kitchen and flames at one of its windows', confidence: 0.7, evidence_ids: [ev(1, 4, 11), ev(2, 3, 9)], refs: ['fire-1', 'fire-2'] },
        { id: 'con-3', text: 'Consider checking for gas near the garage before using the garage door', rationale: 'A possible gas smell was reported with low confidence by one caller', confidence: 0.4, evidence_ids: [ev(2, 40, 44)], refs: ['haz-1'] })
      out.push({ key: 'S4', label: 'S4 · Fairdale (2 calls)', versions: [v1, v2] })
    }
  }
  return out
}
