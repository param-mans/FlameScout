// Extras of a floor plan that Matterport does not provide, computed ONCE with the viewer's own logic and then read by
// everyone (plan index builder, viewer, planner), never recomputed:
//   plan.stairs[]          one entry per stair flight paired between consecutive floors (public/house-viewer/escaleras.js)
//                          { id: "stairs-<lower staircase room id>", lower_floor_id, upper_floor_id, lower_room_id,
//                            upper_room_id, state: ok | no_cabe | dudosa, footprint_scale, direction: 1 | -1 | null,
//                            direction_confidence: clara | estimada | null }
//   opening.exterior       true if one side (0.3 m away) is a room and the other is outside every room (evac.js rule),
//                          for every opening type and floor
// The ids are stable because Matterport room ids are stable.
import { analizar } from "../public/house-viewer/escaleras.js";
import { aperturaExterior, contextoPlanta } from "../public/house-viewer/evac.js";

/** Returns a copy of the plan with stairs[] and an exterior flag on every opening. */
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
  return out;
}
