# Field notes: data quirks

Notes from building `data/raw/` (2026-11-14) from the CoStar hackathon kit, the Matterport GraphQL API and Vexcel API 2.0.

## Matterport (GraphQL Model API)

- **`lowerElevation` is measured from the model base, not from the opening's floor.** On upper floors, subtract that
  floor's level. We use the median `lowerElevation` of the floor's doorways as its base: a door sill is close to 0
  above its own floor.
- **`dimensionEstimates` comes in feet by default.** Ask for `dimensionEstimates(units: metric)` to get m and m²; the
  query response carries `units`.
- **Room outlines are not given as polygons.** A room's `boundary` is a set of edges. The outline is assembled by
  tracing them: vertices joined by id, or by a 2 cm cell if the id is missing; T-junction splitting; walking the
  least-turn loops. Then it is validated (unused edges, self-intersections, area ratio against the API area
  0.8–1.3). `polygonQuality` is `ok`, `unreliable` (ignore the polygon, trust only `areaM2` and `bbox`) or `missing`.
  Outlines run along the wall axis, roughly 5 % larger than the API area.
- **Edges have `type` `wall` or `invisible`.** Invisible edges are open boundaries between rooms (e.g. living room to
  hallway). They are passable and should not be drawn as walls, even though the API still gives them a `thickness`.
- **Openings are `doorway`, `window` or `divider`.** A divider is an opening-like boundary, not a door; it is
  placed along its wall at `relativeCenter` (0–1 of the wall length), like the others.
- **Stairs exist only through room classification.** A staircase is a room classified `Staircase`; there is no
  stair geometry. Pairing between floors (centroids within 3 m or overlapping outlines), climb direction (entries
  at each end) and footprint fit are our heuristics.
- **No elevators.** Matterport has no elevator entity or classification. An elevator, if scanned, is just another
  room or nothing.
- **Openings can be missing.** In 209217448 (model tvaSot6bAt8) a bedroom has three real windows (seen in the 360°
  panoramas) but no window opening in the plan. A missing opening in the plan is not evidence of a missing window.
- **Panoramas.** `panoLocations` gives each sweep's position in model space and an anchor with floor and room ids
  matching the floor plan. Skybox face URLs are signed and expire: never store them.
- Read-only: only queries. Never send `unlockModelBundle` or any other mutation; they buy add-ons and are billed.

## Vexcel API 2.0

- Authentication is the `X-API-KEY` header (the "Header" security scheme). Do not put the key in URLs.
- `bbox-dimensions` is in **meters**, not pixels. For the ortho we request the box that 1024 px cover at zoom 20 at
  that latitude (about 132 m in Houston); for obliques, 90 m.
- Oblique direction is chosen with `product-type=oblique-north|east|south|west`. It is the direction the **camera
  faces**, so `oblique-south` shows north-facing facades.
- Add `order-by=collection-last-capture-date-desc,image-center-distance-asc`. Without the second key, the newest
  collection may return a photo where the house is near the edge, or dark.
- Coverage queries (`ortho/collections`, `oriented/collections`) give collection, layer and capture dates without
  downloading an image.

## CoStar listings

- **"Sprinkler" in a listing usually means lawn irrigation**, e.g. "Sprinkler System" under Lot Details, or "6 Zone
  Sprinkler System" next to the lawn in the remarks (199958956). Only **"Fire Sprinkler System"** (Home Security) counts
  as fire sprinklers.
- Listing `stories` (`resoValues.structure.stories`) is often missing.
- Residential baths: `bathsMin` can be fractional (3.5 = 3 full + 1 half); compare `bathsFull + bathsHalf` with plan
  bathroom rooms.
- Commercial occupancies: current ones have no `toDate`. Tenant names come from `company-detail.jsonl` through
  `tenantLocationId`.

## Stored plan extras and plan index (schema)

Matterport has no stair entity and no exterior flag on openings. Both are computed **once** with the viewer's logic and
stored in `matterport/plan.json`, so the plan index builder, the viewer and the planner read them and nobody
recomputes them. In the app they are added by the same code (`apps/web/server/plan-extras.mjs`).

- `stairs[]`: one entry per stair flight paired between consecutive floors (`public/house-viewer/escaleras.js`:
  Staircase rooms with centroids within 3 m or overlapping outlines).
  `{id, lower_floor_id, upper_floor_id, lower_room_id, upper_room_id, state, footprint_scale, direction,
  direction_confidence, lower_floor, upper_floor, direction_reason, status, confidence, evidence_ids}`.
  - `id` is `stairs-<lower staircase room id>`. It is stable because Matterport room ids are stable, and a lower
    staircase room pairs with at most one upper room.
  - `state` is `ok` (the footprint fits inside its room and the house), `no_cabe` (does not fit, even at 70 %) or
    `dudosa` (unreliable room outline).
- `stair_rooms[]` (rooms classified Staircase) and `stairs_connected[]` (per pair of consecutive floors) keep the
  rest of the former `stairs` object.
- Every door, window and divider has `exterior` (bool) and `floor_id`. An opening is exterior when one side, 0.3 m
  away, is a room and the other is outside every room. This is the rule of `evac.js`, for every opening type and floor.
  An upper-floor door to a balcony is exterior too; only ground-floor exterior doors are evacuation exits.
- Every room has `floor_id`.
- `derived/plan_index.json` is built from `plan.json` only by `scripts/make_plan_index.py`:
  `{floors: [floor ids], rooms: {id: {floor_id, label, type}}, openings: {id: {type, exterior, floor_id}},
  stairs: [ids]}`.
  - Void rooms are left out.
  - `label` is the room label or, if empty, its type, upper case.
  - `type` is the Matterport type, lower case, or null when untyped.
  - The viewer builds the same index from the plan it loads, and a test checks they are identical.
