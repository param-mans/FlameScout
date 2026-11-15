# Call fixtures (synthetic transcripts, text only)

Scenario files live in `data/fixtures/calls/<stem>.json`, in the same format as s1–s6. The schema is
`{script_id, description, segments: [{call_id, speaker: call_taker|caller, text, t_start, t_end}]}`, with each call's
clock starting at 0. The facts a good agent should extract are in `data/fixtures/calls/expected/<stem>.json`. They sit
one folder down because the API lists every `calls/*.json` file as a script (`manifest.json` sits in the same folder
and must be excluded by anything that globs scripts; see "Manifest" below).

All voices are synthetic: invented first names, no phone numbers, no emergency numbers.

| Stem | Building | Calls | What it tests |
|---|---|---|---|
| `s6_fairdale` | 9660 Fairdale Lane, hero `209217448` (scan) | 2 | Floor contradiction for the children (loft upstairs vs a bedroom downstairs); people at floor level and in a room (the office); gas at the separate garage |
| `s7_fairhope_oak` | 18107 Fairhope Oak Street, hero `214905148` (scan) | 3 | Three callers about one garage fire. "Over the kitchen" and "next to the bonus room" resolve to one bedroom through plan relations; "above the garage" (6 rooms) and "front left" stay at floor level |
| `s8_playa_lucia` | 13722 Playa Lucia Court, hero `199958956` (scan, one floor) | 1 | "The room next to the kitchen" matches 7 rooms, so no room is picked; open questions about the exit and the dog |
| `s9_nwtc` | Northwest Trade Center, 9700 Tanner Rd, CoStar `23296` (no scan) | 2 | People count contradiction (3 vs two at most); floor-level locations only; a hazard reported by the caller (forklift batteries) |
| `s10_wilshire` | Wilshire Tower, fixture `fake_wilshire_tower` | 6 | Five ways to say floor 6 (sixth floor, floor six, six up counting the restaurant, right under the roof, at the top) should give one value; call 6 is another incident (a car fire) |

## Showcase scripts

Short, fully consistent calls for demos: one call, 2-3 segments, at most 10 s and 28 words. Every fact resolves on the
real plan with `resolve_place` (fire and people each land in exactly one room). No front/back/left/right.

| Stem / id | Building | Text (caller / call taker) | Resolves to |
|---|---|---|---|
| `show_fairdale` / `SHOW_FAIRDALE` | 9660 Fairdale Lane, `209217448` | "9660 Fairdale Lane! Kitchen's on fire, first floor, gas stove." / "Anyone inside?" / "My two kids, upstairs in the loft!" (19 words, 8.0 s) | fire: kitchen `j7nhq2abkp0` (floor 1); kids: loft `n2yqr30509t7i8whfdf2htwic` (floor 2); hazard: gas |
| `show_fairhope` / `SHOW_FAIRHOPE` | 18107 Fairhope Oak Street, `214905148` | "18107 Fairhope Oak Street! Media room's on fire, second floor." / "Anyone inside?" / "My dad's in the bedroom next to the bonus room, on oxygen!" (24 words, 9.6 s) | fire: media room `n81qfe51azs8iexnbqwspye6c` (floor 2); father: bedroom next to the bonus room `peina19mpqedsku6a3huu38md`; hazard: oxygen (LLM only) |
| `show_playa` / `SHOW_PLAYA` | 13722 Playa Lucia Court, `199958956` (one floor) | "13722 Playa Lucia Court! The garage is on fire, gas cans inside." / "Anyone in the house?" / "My grandma, in the den. She can't get out!" (25 words, 9.3 s) | fire: garage `i9ib8b4tvob`; grandmother (trapped): den `kogpvjihnk0`; hazard: gas |

| `show_fd_dryer` / `SHOW_FD_DRYER` | Fairdale, `209217448` | "9660 Fairdale Lane! The dryer's on fire!" / "Who's inside?" / "My husband's in the office. My daughter's in a bedroom downstairs." (20 words, 8.6 s) | fire: laundry `akdbyao21yx` (inferred from "dryer", far from the doors); husband: office (green); daughter: 3 bedrooms on floor 1 (yellow) |
| `show_fh_bonus` / `SHOW_FH_BONUS` | Fairhope, `214905148` | "18107 Fairhope Oak Street! Fire in the bonus room, second floor!" / "Anyone inside?" / "Grandpa was sleeping upstairs. My wife's in the dining room." (23 words, 9.4 s) | fire: bonus room `9sy6311hky7` (floor 2); grandfather: 4 bedrooms on floor 2 (inferred from "sleeping", yellow); wife: dining room (green) |
| `show_pl_office` / `SHOW_PL_OFFICE` | Playa, `199958956` | "13722 Playa Lucia Court! The office is on fire!" / "Anyone inside?" / "Mom's in the living room, and my son's in the bathroom." (22 words, 8.2 s) | fire: office `dsaby2t1uer`; mother: living room (green); son: 5 bathrooms (yellow) |

Catalog houses (`data/raw/catalog.csv`, not viewer samples): the plan comes from the house data service
(`GET /api/house-viewer/plan/<id>`, live read-only Matterport) and is cached by `services/agents/tools/house_data_client.py`
in `data/raw/<id>/matterport/plan.json` and `derived/plan_index.json`. The spoken address auto-confirms the listing
through `house_catalog.match` (one candidate, score 0.9). The web shows these houses through the same service
(`/api/house-viewer/sample/<id>`) without adding them to `samples/index.json`.

| Stem / id | Building | Text (caller / call taker) | Resolves to |
|---|---|---|---|
| `show_ef_stove` / `SHOW_EF_STOVE` | 5815 Effingham Drive, `226579006` (one floor, 1424 sqft) | "5815 Effingham Drive! The stove's on fire!" / "Anyone inside?" / "My son's in the garage. Grandma's in a bedroom." (18 words, 7.6 s) | fire: kitchen `xy2x8mpaaqknp7q2c3nnbw1ma` (inferred from "stove", 9.5 m from the nearest exterior door); son: garage (green); grandmother: 2 bedrooms (yellow) |
| `show_om_office` / `SHOW_OM_OFFICE` | 4830 Omeara Drive, `227423982` (one floor, 2131 sqft) | "4830 Omeara Drive! The office is on fire!" / "Who's inside?" / "My wife's in the kitchen, and two kids in a bedroom." (21 words, 7.7 s) | fire: office `2qdmc5i9byxi79ry1pxdkqzea` (7.2 m from the nearest exterior door); wife: kitchen (green); two kids: 4 bedrooms (yellow) |
| `show_gp_pan` / `SHOW_GP_PAN` | 13247 Gatton Park Drive, `217757820` (two floors, 2380 sqft) | "13247 Gatton Park Drive! A pan's on fire downstairs!" / "Anyone inside?" / "My wife's in the bonus room. My son's in a bedroom upstairs." (23 words, 9.4 s) | fire: kitchen `cdz3fkt38kae7tapstpt0eaeb` (floor 1, inferred from "pan", 6.2 m from the nearest door); wife: bonus room (green, floor 2); son: 5 bedrooms on floor 2 (yellow) |
| `show_en_laundry` / `SHOW_EN_LAUNDRY` | 3218 Enclave Lane, `208401769` (two floors, 3076 sqft) | "3218 Enclave Lane! Laundry room's on fire, upstairs!" / "Who's inside?" / "Dad's in the office. My daughter's in a bathroom upstairs." (20 words, 8.4 s) | fire: laundry `n2yqr30509t7i8whfdf2htwic` (floor 2, 10.9 m from the nearest ground-floor door); father: office (green, floor 1); daughter: 4 bathrooms on floor 2 (yellow) |
| `show_sr_upstairs` / `SHOW_SR_UPSTAIRS` | 14020 Schroeder Road, `182022608` (two floors, 1178 sqft) | "14020 Schroeder Road! The bedroom upstairs is on fire!" / "Anyone inside?" / "Mom's in the loft. My brother's in the garage." (20 words, 7.9 s) | fire: bedroom `lbbhtoyakj6` (the only one on floor 2, reached by the stairs); mother: loft next to it (green); brother: garage (green). No yellow: every room type is unique per floor |


In the last three and the catalog ones the fire is away from the exterior doors, and each (except `SHOW_SR_UPSTAIRS`) mixes green people (one room) with yellow ones (a
description that matches several rooms: a candidate in each, `candidate_room_ids` in the expected file).

The keyword extractor gets the address, flames and, where said literally, the floor (Fairdale 1, Fairhope 2) and the
gas hazard; Playa adds `people.trapped`. Each showcase expected file also has a top-level `floor` (fire floor) and a
`resolve` query on every fire/people entry, which the tests pass straight to `resolve_place`.

## Manifest (what the UI lists)

`data/fixtures/calls/manifest.json` is the contract read by `GET /api/calls/scripts` to filter the list:

```json
{"scripts": [{"id": "SHOW_FAIRDALE", "title": "SHOWCASE · 9660 Fairdale Lane", "group": "showcase",
              "house": "209217448", "inconsistent": false, "visible": true}]}
```

- `group` is `showcase` or `test`; showcases come first and their titles start with `SHOWCASE · `.
- `house` is the listing id in `apps/web/public/house-viewer/samples/index.json` (`id`) or in the house catalog
  `data/raw/catalog.csv` (`listing_id`, checked only when the file exists), or `null` for fixture buildings.
- Exactly one visible script is `inconsistent` and its title contains `INCONSISTENT`.
- Scripts that are missing from the manifest or have `visible: false` are hidden from the UI but still load by id.

Visible: `SHOW_FAIRDALE`, `SHOW_FAIRHOPE`, `SHOW_PLAYA`, `SHOW_FD_DRYER`, `SHOW_FH_BONUS`, `SHOW_PL_OFFICE`, `SHOW_EF_STOVE`,
`SHOW_OM_OFFICE`, `SHOW_GP_PAN`, `SHOW_EN_LAUNDRY`, `SHOW_SR_UPSTAIRS`, `S6_FAIRDALE` (the only inconsistent one: callers disagree on
the children's floor), `S7_FAIRHOPE_OAK` (3 callers, same fire, consistent) and `S8_PLAYA_LUCIA` (vague caller: yellow
candidates in every matching room). Hidden: S1, S2, S3, S4, S4B, S4Q, S5, S6, S9_NWTC, S10_WILSHIRE.

## Expected files

Each expected file has two levels:
- `any_extractor`: facts in the fusion keys that any extractor must get, because the callers say them literally.
- `llm_only`: what only a language model can get: people with counts and precision, plan room ids, contradiction
  groups, separate incidents and open questions.

Two deviations from the brief:
- The CoStar record for 23296 says **9700** Tanner Rd (9750 only appears in floor-plan URLs).
- The Wilshire fixture has six storeys with a ground-floor restaurant, so its fire floor is 6, not 4.

## How to load

- Tests and code: `load_script("S7_FAIRHOPE_OAK")` in `tests/conftest.py`, the same path as the existing scripts.
- API: `POST /api/calls/simulate {"script_id": "S7_FAIRHOPE_OAK"}` (script ids are up to 16 characters).
- Scripts: `python scripts/run_fusion.py S7_FAIRHOPE_OAK`. It writes to `workspace/` and loads `.env`.

`tests/test_call_fixtures.py` checks the manifest contract, the showcase limits and their resolution on the real plan,
the structure of every scenario, that the new ones load like the old ones, that
the expected room and floor ids exist in `data/raw` (skipped without it), and that fusion accepts each scenario in
memory.

## What cannot run end to end yet

- **Room-level placements, people counts, contradiction groups and the car fire in s10 need a model.** Without one
  (keyword extractor, or no `GEMINI_*` keys), the orchestrator writes a facts-only brief and an empty fallback overlay:
  - fusion has no keys for people counts or locations;
  - one script is one incident, so s10's call 6 is merged into the tower incident.
- **s6–s9 need the hero data in `data/raw`, which is local and not in git.** With it, the locator puts the right building
  first for all five scenarios. s10 runs anywhere from the committed fixtures.
- **s9 has no interior scan:** its locations stay at floor level and no route can be planned.
