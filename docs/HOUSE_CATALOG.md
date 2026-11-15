# House catalog, on-demand plans and the house screen

## Catalog (`data/raw/catalog.csv`, local, not in git)

Metadata only, one row per house with an `ok` plan (568 houses, ~88 KB): listing_id, matterport_model_id, address,
city, state, zip, floors, bedrooms, bathrooms, sqft, year_built, room_types (`|`), exterior_doors, stairs, quality.
Built once with `node scripts/build_house_catalog.mjs` from `../demo_costar` (quality csv, the hackathon kit and the
generated plans, read only to summarise room types, floors and stairs). No plan is copied. Copy it to the server's
`data/raw/` with the rest of the raw data.

## Plans on demand

When the confirmed building is a catalog house with no `data/raw/<listing_id>/` folder, the backend asks the house data
service (`apps/web/server`, `GET /api/house-viewer/plan/<id>` → `{plan, plan_index}`, Matterport live + plan-extras)
through `services/agents/tools/house_data_client.py` (15 s timeout) and caches the answer in
`data/raw/<listing_id>/matterport/plan.json` and `derived/plan_index.json`. Any failure = "No interior scan on file";
the pipeline goes on without a plan. The service serves ids from `samples/index.json` and from the catalog only.

| Variable | Default | Use |
|---|---|---|
| `HOUSE_DATA_URL` | `http://127.0.0.1:8080` | house data service for the backend; `off` disables (tests) |
| `HOUSE_CATALOG_CSV` | `<HOUSE_DATA_RAW>/catalog.csv` | catalog read by `standalone.mjs` (locally set `HOUSE_DATA_RAW=$PWD/data/raw`) |

## Address locator

`services/agents/house_catalog.py` normalises addresses (case, punctuation, St/Ln/Ct/Dr/Rd/Blvd/Ave, N/S/E/W, spoken
digits with "oh"/"zero": "nine six six oh Fairdale" = 9660) and scores house number, street and city. A said house
number must match. The existing auto-confirm rule (≥ 0.8 with a 0.2 margin, or a single candidate) decides; a street
without a number and several houses needs the dispatcher. An address not in the catalog leaves the incident without a
house and adds the Copilot suggestion "Address not in catalog".

## Fast path: inference and candidates

- Inference (code-enforced): cooking/eggs/pan/stove → kitchen, shower/bathtub → bathroom, sleeping/bed → bedroom,
  car/tools → garage, clothes/dryer → laundry. Only when the plan (the said floor, if any) has exactly one such room:
  status `inferred`, confidence ≤ 0.6, note `inferred from: <cue>`; otherwise floor level.
- Candidates: a description matching several rooms ("the bathroom", or an inferred type with several rooms: "sleeping"
  with 3 bedrooms) gives one occupant per room, same count and `group_id`, status `contradicted`, plus a
  `contradictions` entry (yellow on screen). A named type limits the candidates to that type ("a bedroom downstairs":
  the bedrooms of floor 1 only, also when only the caller's words or the note carry the type). A person with no room
  type at all ("I think she's in one of the rooms", "upstairs") gets a candidate in every room of that floor (or of
  the house) a person can be in: no closets, hallways, stairs, porches.
- Scripts: `TEST_COOKING` (visible, "TEST · cooking, room not named (inferred)") and the hidden `AMBIG_BATHROOMS`
  (ids are limited to 16 characters).

## House screen (layout=house)

- Green figures: occupants in one room. Yellow: each candidate room of a contradicted group, and floor-level reports
  (one figure in the floor's at_stairs room, else its largest). Up to 5 figures per entity, no text.
- Fire: radial red glow (radius min(8 m, 35 % of the floor's largest side)) clipped to the house outline, 2D and 3D.
- Routes: red entrance → fire room; green entrance → every person (shortest order) → exit, avoiding the fire. Rooms the scan left without a doorway are
  joined to the nearest connected room (penalised) so nobody is left out of the green route. Between floors the routes climb
  along the detected stair run (foot to head), drawn as a thick line in 3D. A tapped room shows its fastest exit in white. Hidden attributes on the viewer `<body>`: `data-people-green`,
  `data-people-yellow`, `data-route-red`, `data-route-green`, `data-unreachable`.
- Cards: Stairwells, Hazard flags (only hazards this house has; no pool) and PEOPLE, large type.
- PEOPLE card: total reported (an ambiguous group counts once) and the rooms on one line; a dash without overlay.
- Test: `node apps/web/scripts/house-people.mjs` (WebKit, 1180x820), examples in
  `apps/web/public/house-viewer/incident/examples/fairdale_*.json`.
