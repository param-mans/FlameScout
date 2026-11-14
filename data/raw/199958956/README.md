# 13722 Playa Lucia Court, Houston, TX 77044

- **Listing id:** 199958956 (CoStar residential listing; listed 2025-06-28)
- **Matterport model:** 71svY4RB8jP
- **Location:** 29.962922, -95.161238
- **Type:** Detached, built 2010
- **Quality check:** ok; coverage ratio 1
- **Exterior exits:** 3; stairs: none (one floor)

Generated 2026-11-14.

## Contents and sources

| Path | Source |
| --- | --- |
| `costar/listing.json` | Record of `data/residential-listings.jsonl` (CoStar hackathon kit), as is |
| `costar/listing_features.json` | Record of `data/residential-listing-features.jsonl`, as is |
| `costar/residential-listings_row.csv` | Header and row of `residential-listings.csv`, as is |
| `matterport/plan.json` | Normalized plan from `demo_costar/web/plans/gen/71svY4RB8jP.json`, generated from the Matterport GraphQL API. Includes rooms, floors, doors, windows, exterior exits, stairs (`web/escaleras.js`), quality (`web/data/calidad.csv`) and coverage ratio. Every fact has `status`, `confidence` and `evidence_ids` that resolve in `evidence` |
| `matterport/raw_floors.json` | Raw response of the read-only Matterport GraphQL floors query (edges, openings, rooms) that `plan.json` comes from. No URLs, tokens or keys |
| `derived/plan_index.json` | Plan index built from `plan.json` by `scripts/make_plan_index.py` |
| `vexcel/metadata.json` | Vexcel API 2.0, read-only coverage queries: ortho and 4 obliques (collection, capture date, layer, request parameters, box size in meters). No images |
| `derived/plan.svg`, `derived/plan.png` | Plan drawn from `matterport/plan.json`: white background, black lines, room names |
| `derived/plan_ui.svg` | Same plan in the viewer's dark style |

Vexcel: ortho urban / us-tx-baytown-2026, captured 2026-02-07, 132.4 m box;
obliques north 2026-02-07, east 2026-02-07, south 2026-02-07, west 2026-02-07 (90 m box).

## Listing vs plan

| | Listing | Plan | Difference |
| --- | --- | --- | --- |
| Floors | 1 | 1 | match |
| Bedrooms | 4 | 3 | -1 |
| Bathrooms | 3.5 (3 full, 1 half) | 4 | match |
| Area (sq ft) | 3724 | 4185 | +12% |

Plan bedrooms and bathrooms are rooms typed `Bedroom` and `Bathroom` by Matterport's AI classifier, which can be
wrong. Plan area is the sum of room areas (not Void, at least 2 m²) in sq ft. The listing area is `sqftMin`. Floors
in the listing come from `resoValues.structure.stories`.

## Sprinklers

The listing mentions a "Sprinkler System" under *Lot Details* (features, section "Lot"), and the remarks say
"Well Maintained Lawn with Fenced Backyard & 6 Zone Sprinkler System". This is **lawn irrigation, not a fire sprinkler
system**. Only "Fire Sprinkler System" under Home Security counts as fire sprinklers (see ../FIELD_NOTES.md).
