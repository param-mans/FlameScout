# 9660 Fairdale Lane, Houston, TX 77063

- **Listing id:** 209217448 (CoStar residential listing; listed 2025-11-20)
- **Matterport model:** tvaSot6bAt8
- **Location:** 29.730995, -95.538914
- **Type:** Detached, built 1999
- **Quality check:** ok — tags: dormitorio_sin_ventana; coverage ratio 1
- **Exterior exits:** 5; stairs: 1-2 ok (scale 1, direction clara)

Generated 2026-11-14.

## Contents and sources

| Path | Source |
| --- | --- |
| `costar/listing.json` | Record of `data/residential-listings.jsonl` (CoStar hackathon kit), as is |
| `costar/listing_features.json` | Record of `data/residential-listing-features.jsonl`, as is |
| `costar/residential-listings_row.csv` | Header and row of `residential-listings.csv`, as is |
| `matterport/plan.json` | Normalized plan from `demo_costar/web/plans/gen/tvaSot6bAt8.json`, generated from the Matterport GraphQL API. Includes rooms, floors, doors, windows, exterior exits, stairs (`web/escaleras.js`), quality (`web/data/calidad.csv`) and coverage ratio. Every fact has `status`, `confidence` and `evidence_ids` that resolve in `evidence` |
| `matterport/raw_floors.json` | Raw response of the read-only Matterport GraphQL floors query (edges, openings, rooms) that `plan.json` comes from. No URLs, tokens or keys |
| `derived/plan_index.json` | Plan index built from `plan.json` by `scripts/make_plan_index.py` |
| `vexcel/metadata.json` | Vexcel API 2.0, read-only coverage queries: ortho and 4 obliques (collection, capture date, layer, request parameters, box size in meters). No images |
| `derived/plan.svg`, `derived/plan.png` | Plan drawn from `matterport/plan.json`: white background, black lines, room names |
| `derived/plan_ui.svg` | Same plan in the viewer's dark style |

Vexcel: ortho urban / us-tx-houston-2026, captured 2026-01-02, 132.8 m box;
obliques north 2026-01-02, east 2026-01-02, south 2026-01-02, west 2026-01-02 (90 m box).

## Listing vs plan

| | Listing | Plan | Difference |
| --- | --- | --- | --- |
| Floors | — | 2 | not in listing |
| Bedrooms | 3 | 3 | match |
| Bathrooms | 3 (3 full) | 3 | match |
| Area (sq ft) | 3480 | 3653 | +5% |

Plan bedrooms and bathrooms are rooms typed `Bedroom` and `Bathroom` by Matterport's AI classifier, which can be
wrong. Plan area is the sum of room areas (not Void, at least 2 m²) in sq ft. The listing area is `sqftMin`. Floors
in the listing come from `resoValues.structure.stories`.

## Bedroom window check (tag `dormitorio_sin_ventana`)

The quality check tagged this house because one bedroom has no window within 0.5 m of its outline. Data and thresholds
were not changed.

### 1. Plan

| Bedroom | Floor | m² | doors / windows ≤ 0.5 m | doors / windows ≤ 1.5 m | exterior walls |
| --- | --- | --- | --- | --- | --- |
| d3slsxy3zlg | 1 | 15.59 | 4 / 0 | 5 / 0 | 2 (8.32 m) |
| tt6gm2a0463x9wmnx7rn6gugc | 1 | 20.92 | 3 / 2 | 4 / 2 | 2 (6.49 m) |
| 2qdmc5i9byxi79ry1pxdkqzea | 1 | 16.08 | 4 / 2 | 5 / 2 | 1 (4.77 m) |

The bedroom without a window is **`d3slsxy3zlg`**: floor 1, 15.59 m², reliable outline, centroid
(-5.01, -6.76) in model space. It has 2 exterior walls
(8.32 m) and no window opening within 1.5 m. A wall is exterior when a point 0.3 m from it, on
one side, is outside every room.

Openings within 1.5 m of its outline:

| Type | Distance (m) | Width (m) | Sides (rooms 0.3 m each side) |
| --- | --- | --- | --- |
| doorway | 0.00 | 0.79 | Bedroom (d3slsx) ↔ Hallway (fpmc7x) |
| doorway | 0.81 | 0.77 | Bedroom (2qdmc5) ↔ Hallway (fpmc7x) |
| doorway | 0.41 | 0.57 | Hallway (fpmc7x) ↔ Bathroom (b4qtyk) |
| doorway | 0.00 | 0.58 | Bathroom (b4qtyk) ↔ Bedroom (d3slsx) |
| doorway | 0.00 | 0.58 | Closet (ediidm) ↔ Bedroom (d3slsx) |

### 2. Panoramas (Matterport `panoLocations`, read-only)

The model has 99 pano locations. Four are inside this bedroom, and their anchor room is the same room id. Nearest
locations:

| Pano | Distance to centroid (m) | Anchor room | Inside bedroom polygon |
| --- | --- | --- | --- |
| 37c3mt18e3… | 0.16 | Bedroom(d3slsx) | yes |
| hnhtbkmiig… | 1.91 | Bedroom(d3slsx) | yes |
| n6gf8m67ia… | 2.04 | Bedroom(d3slsx) | yes |
| umd9aznzu6… | 2.14 | Bedroom(d3slsx) | yes |
| m6ye4ky56u… | 2.75 | Bedroom(2qdmc5) | no |
| 2p5wyzeus1… | 2.97 | Bathroom(b4qtyk) | no |

4 skybox faces (the horizontal ones, 2k) of the central pano `37c3mt18e3…` were downloaded, 0.16 m from the
centroid. No locked-bundle error. The images were viewed and are not stored here or in git.

| Face | What it shows | Bedroom window visible |
| --- | --- | --- |
| 1 | Doors to the bathroom and to the hallway (stair railing visible): the bedroom's two doors in the plan | no |
| 2 | Blank wall corner | no |
| 3 | Blank wall, with the edge of a window with blinds at the right | yes (edge) |
| 4 | **Three large double-hung windows with blinds**, looking out on the front lawn and street; next to them the door to the walk-in closet (the plan's Closet door of this bedroom) | **yes** |

### 3. Viewer

`derived/bedroom_window_check.png`: demo viewer with this bedroom selected (highlighted in the 2D plan, inspector open),
with its doors and the plan's tag "A bedroom has no window near its outline".

### 4. Conclusion

**Real window not detected (floor plan error).** The bedroom has three windows on its exterior wall that the
Matterport plan does not contain as openings. The `dormitorio_sin_ventana` tag is a false positive for this house.

Demo sentence: *"The scan's floor plan shows no window in this bedroom, but the 360° photos taken inside it show three
large windows on the outside wall — treat the plan's openings as incomplete and verify on arrival."*
