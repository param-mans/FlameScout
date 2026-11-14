# Northwest Trade Center, 9700 Tanner Rd, Houston, TX 77041

- **CoStar property id:** 23296 (commercial)
- **Location:** 29.850575, -95.533213
- **No interior scan used.** The kit links a Matterport model to this property; it was deliberately not used.

Generated 2026-11-14.

## Contents and sources

| Path | Source |
| --- | --- |
| `costar/property.json` | Record of `data/commercial-property.jsonl` (CoStar hackathon kit), as is |
| `costar/occupancies_current.json` | Records of `data/location-occupancy.jsonl` for this property with no `toDate` (2 of 19) |
| `costar/tenant_companies.json` | Records of `data/company-detail.jsonl` for the tenants of those occupancies (at most 30): Centerline Manufacturing, Spring Engineers Inc |
| `costar/floorplan_urls.json` | Plan attachments of the property record (1: attachment type 10 = floor plan, 12 = site plan) |
| `derived/plan_index.json` | Empty plan index (`{"floors": [], "rooms": {}, "openings": {}, "stairs": []}`): no interior scan is used, so an incident overlay for this building can only use `floor` (with `floor_reported`) and `unknown` locations, and has no routes (docs/INCIDENT_OVERLAY_SPEC.md, "Buildings without a scan") |
| `vexcel/metadata.json` | Vexcel API 2.0 metadata from the demo's local cache (fetched earlier, read-only): ortho and 4 obliques. No images |

Vexcel: ortho urban / us-tx-cypress-2026, captured 2026-01-29, 132.6 m box.
