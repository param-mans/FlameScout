# Incident Overlay contract (agents to 3D viewer)

Goal: the 3D/2D viewer shows what the calls reported, on top of the building plan.
We define the output first. The agents are built to produce exactly this.

## Principle

- Agents decide WHAT was reported (who, what, which room, how sure, evidence).
- Code decides WHERE and HOW (geometry, routes, distances).
- The viewer decides how it LOOKS.

Agents never output coordinates, polygons or route geometry. They output ids from the plan,
plus semantic anchors ("near window w7"). If an id does not exist in the plan, the overlay is rejected.

## Who produces what

| Piece | Producer | Notes |
|---|---|---|
| Occupants, fire, hazards, blocked elements | Agent (from call transcripts) | Always `reported` or weaker, never `verified` by a caller alone |
| Objectives (rescue / recon) and their priority | Agent | References occupants or rooms, with evidence |
| Considerations (text for the side panel) | Agent | Advisory wording only |
| Routes | Deterministic planner (code) | Agent only supplies objectives. `produced_by` must be `planner` |
| Heat maps, figures, labels | Viewer | Pure rendering of the overlay |

## What the agent receives (plan index)

The viewer side exports a small index of the scanned building. The agent must use it (tool or context)
to resolve phrases like "the bedroom over the kitchen" into ids.

```json
{ "floors": ["f1","f2"],
  "rooms": {"r9": {"floor_id":"f2","label":"BEDROOM","type":"bedroom"}},
  "openings": {"d1": {"type":"doorway","exterior":true,"floor_id":"f1"}},
  "stairs": ["s1"] }
```

If a phrase cannot be resolved to one room, use a coarser location (floor) or `unknown`.
Never guess a room to look precise.

## IncidentOverlay fields

Top level: `incident_id`, `model_id`, `version` (int, +1 on each change), `as_of` (ISO 8601),
`changes_since_last` (list of strings), `occupants[]`, `fire[]`, `hazards[]`, `blocked[]`,
`contradictions[]`, `objectives[]`, `considerations[]`, `routes[]` (filled by the planner).

Common to occupants, fire, hazards and blocked:
`id`, `status` (verified | inferred | reported | unverified | contradicted), `confidence` (0 to 1),
`reported_at` (ISO 8601), `evidence_ids` (transcript spans, required unless status is `unverified`), `note` (optional).

- `occupants[]`: `count` (int >= 1) or `count_range` [min, max], `location`, optional `group_id`.
- `fire[]`: `kind` (smoke | fire | heat), `location`.
- `hazards[]`: `kind` (gas | chemical | battery | electrical | structural | other), `location`.
- `blocked[]`: `ref_id` of a door, stairs or room reported impassable.

### Location (precision levels)

| kind | Required fields | Meaning |
|---|---|---|
| `room` | `room_id` | In that room |
| `anchor` | `anchor.room_id`, `anchor.position`, `anchor.ref_id` for near_* | Inside the room, near a window, door, stairs or one side |
| `floor` | `floor_id` or `floor_reported` (caller's floor number, ground floor = 1) | Floor known, room unknown |
| `unknown` | none | Reported but not locatable |

`anchor.position`: center, near_door, near_window, near_stairs. There is no orientation in the contract: no cardinal sides (north/south/east/west) and no front/back/left/right.
Map `floor_reported` to `floor_id` only when the scan covers that floor, otherwise keep only the reported number.

### Contradictions

When callers disagree, emit one entity per candidate, each with `status: contradicted` and the same
`group_id`, plus one entry in `contradictions[]` (`group_id`, `key`, `resolved_by_dispatcher`).
Never pick a winner. The dispatcher resolves it.

### Objectives

`id`, `type` (rescue | recon), `priority` (int >= 1), `target` {`kind`: occupant | occupant_group | room | floor, `ref_id`} (for kind `floor`, `floor_reported` can replace `ref_id`),
`rationale`, `evidence_ids`. The planner turns each objective into a route.

### Considerations

`id`, `text` (must start with "Consider"), `rationale`, `confidence`, `evidence_ids`, `refs` (ids in this overlay).
No directive wording (must, immediately, 100%, ordered, guaranteed).

### Routes (planner output)

`id`, `objective_id`, `produced_by: "planner"`, `kind`, `status` (inferred), `confidence`,
`waypoints` [{`kind`: exterior | room | opening | stairs, `ref_id`}] (at least 2),
`length_m_estimate`, `passes_near_fire`, `note`.
If no route can be computed (unconnected stairs, no exterior exit in the scan) the planner returns no route
and the viewer shows "No route computable". Nobody fills the gap.

## What the viewer draws

Colors are the only saturated colors in the scene. Always paired with an icon and a text label.

| Overlay content | Rendering | When uncertain |
|---|---|---|
| Occupants, room or anchor | Person figures standing in the room (up to 5, then "xN"), green floor glow, vertical beacon visible through other floors, label "N reported, age, source" | `count_range` shows "1-2". `contradicted` shows dashed green in each candidate room with "?" |
| Occupants, floor only | Green tint on that floor outline and a banner "Occupants reported on floor 2 (room unknown)". No figures | |
| Fire, room | Red heat gradient over the room floor plus a translucent heat column, label "FIRE (reported)" | `smoke` uses a grey-red haze instead |
| Fire, anchor | Same, centered on the anchor | |
| Fire, floor only | Red tint on the floor outline and a banner. No point | |
| Hazards | Amber icon in the room with its label | |
| Blocked | Hatched door or stairs | |
| Routes | Animated dashed white path along the waypoints, numbered, tinted red where it passes near fire, length labeled "estimate" | No route: banner "No route computable" |
| Considerations | Cards in the side panel. Clicking one highlights its `refs` | |
| Age | Entities older than 10 minutes fade and show "stale" | |
| Versions | A version slider. Changes since the last version are highlighted | |

Not drawn: fire spread, smoke movement, any occupant that nobody reported.
No simulation, no inference of people from room types.

## Plan index v2: size and spatial relations (no orientation)

The plan has no orientation: no north, no street side, no front/back/left/right. Rooms are described by size and by
relations between rooms, computed once in the plan enrichment (`apps/web/server/plan-extras.mjs`), stored in
`plan.json`, and copied, never recomputed, by `scripts/make_plan_index.py` and the viewer's `incident/plan-index.js`.
A test checks that the two indexes are identical. The index has `"version": 2`. Each room that is not Void has:

| Field | Meaning |
|---|---|
| `area_m2` | Room area from the API (m²) |
| `size_rank` | Rank by area among rooms of the same type on its floor. 1 is the largest; areas within 5 % of the first room of a rank share that rank (competition ranking). |
| `size_of` | Number of rooms of that type on that floor |
| `next_to` | Rooms on the same floor that share a wall of at least 0.5 m: the outline is sampled every 0.25 m with a 0.3 m tolerance, and the shared length is averaged from both sides. The relation is mutual. |
| `over` | Rooms on the floor directly below whose overlap is at least 30 % of the smaller room's area. Overlap is sampled at the centres of a 0.5 m grid and works for non-convex outlines. |
| `under` | The inverse of `over`. Both are computed once per pair, so they are always symmetric. |
| `at_stairs` | Shares a wall or an opening with a Staircase room on its floor. Absent on Staircase rooms. |

Rooms whose `polygonQuality` is not `ok` get empty lists and `at_stairs: false`, and they never appear in other rooms'
lists. Relations do not change when the house is turned 90, 180 or 270 degrees; a test checks this.

### Resolving a place

`packages/schemas/overlay/resolve_place.py` resolves a spoken place to one room only when exactly one room matches.
Otherwise it returns the candidates and a clarifying question, and the location stays at floor level. See
`plan_index.example.json` for the format.

Ties never produce an empty answer: if the smallest rooms are tied (ranks use competition ranking, for example 1, 2, 2), size smallest returns all of them as ambiguous. Questions skip the candidate's own type and generic rooms (closet, hallway) and add 'another one' when a candidate matches none of the options.

## Buildings without a scan

If there is no interior scan, the plan index is empty (`incident_overlay.noscan.example.json` and
`plan_index.noscan.example.json`). The agent can only use `floor` (with `floor_reported`) and `unknown`
locations. Room, anchor and stairs ids are rejected. There are no routes. The viewer shows banners and the
considerations panel, and never a plan: "No interior scan on file".

## Ids

- Rooms, openings and floors use the ids stored in the normalized `plan.json` of each building (they come from
  the Matterport API and are stable).
- `plan.json` carries `stairs[]` with a stable `id` each, and an `exterior` flag on every opening.
  Nobody recomputes them: the plan index builder and the viewer both read them from `plan.json`.
- The plan index of a building is generated from `plan.json` and stored next to it
  (`data/raw/<id>/derived/plan_index.json`). The viewer's own index must be identical (it is compared in a test).

## Transport

- One overlay per brief version. `overlay.version` equals the brief version.
- The agent pipeline writes `workspace/<incident_id>/overlay_vN.json` and emits a WebSocket event
  `overlay.updated` ({incident_id, ts, trace_id, version, overlay}) next to `brief.updated`.
- The web app forwards it to the viewer iframe with `postMessage({type: 'setIncident', overlay}, viewerOrigin)`.
- Agent output always has `routes: []`. The viewer's planner adds the routes on the client.
- The viewer buffers the latest overlay until its plan is loaded, ignores versions older than the current one
  (so replays are harmless) and keeps the received versions for the version slider.
  It answers with `incidentApplied` or `incidentRejected` (with the error list). The host should log rejections.
- The agent's submit step runs `validate_overlay.py` before emitting, so rejections should not happen.

## Validation

`validate_overlay.py overlay.json plan_index.json` (stdlib only) checks every rule above.
The viewer runs the same checks. An invalid overlay is rejected, the previous version stays on screen,
and the problems are logged. Run it in the agent tests with `incident_overlay.example.json`.

## Wording and safety

Advisory only. The incident commander decides. Simulation only, never connected to real emergency lines.
Everything shown is an estimate from caller reports and a partial scan.
