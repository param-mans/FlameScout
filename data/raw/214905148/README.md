# 18107 Fairhope Oak Street, Houston, TX 77084

- **Listing id:** 214905148 (CoStar residential listing; listed 2026-01-23)
- **Matterport model:** Jnn2eWwYXU5
- **Location:** 29.813143, -95.680383
- **Type:** Detached, built 2009
- **Quality check:** ok; coverage ratio 1
- **Exterior exits:** 2; stairs: 1-2 ok (scale 1, direction estimada)

Generated 2026-11-14.

## Contents and sources

| Path | Source |
| --- | --- |
| `costar/listing.json` | Record of `data/residential-listings.jsonl` (CoStar hackathon kit), as is |
| `costar/listing_features.json` | Record of `data/residential-listing-features.jsonl`, as is |
| `costar/residential-listings_row.csv` | Header and row of `residential-listings.csv`, as is |
| `matterport/plan.json` | Normalized plan from `demo_costar/web/plans/gen/Jnn2eWwYXU5.json`, generated from the Matterport GraphQL API. Includes rooms, floors, doors, windows, exterior exits, stairs (`web/escaleras.js`), quality (`web/data/calidad.csv`) and coverage ratio. Every fact has `status`, `confidence` and `evidence_ids` that resolve in `evidence` |
| `matterport/raw_floors.json` | Raw response of the read-only Matterport GraphQL floors query (edges, openings, rooms) that `plan.json` comes from. No URLs, tokens or keys |
| `derived/plan_index.json` | Plan index built from `plan.json` by `scripts/make_plan_index.py` |
| `vexcel/metadata.json` | Vexcel API 2.0, read-only coverage queries: ortho and 4 obliques (collection, capture date, layer, request parameters, box size in meters). No images |
| `derived/plan.svg`, `derived/plan.png` | Plan drawn from `matterport/plan.json`: white background, black lines, room names |
| `derived/plan_ui.svg` | Same plan in the viewer's dark style |

Vexcel: ortho urban / us-tx-houston-2026, captured 2026-01-02, 132.6 m box;
obliques north 2026-01-02, east 2026-01-02, south 2026-01-29, west 2026-01-02 (90 m box).

## Listing vs plan

| | Listing | Plan | Difference |
| --- | --- | --- | --- |
| Floors | 2 | 2 | match |
| Bedrooms | 4 | 5 | +1 |
| Bathrooms | 2.5 (2 full, 1 half) | 4 | +1.5 |
| Area (sq ft) | 3000 | 3185 | +6% |

Plan bedrooms and bathrooms are rooms typed `Bedroom` and `Bathroom` by Matterport's AI classifier, which can be
wrong. Plan area is the sum of room areas (not Void, at least 2 m²) in sq ft. The listing area is `sqftMin`. Floors
in the listing come from `resoValues.structure.stories`.
