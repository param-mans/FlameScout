# Call fixtures (synthetic transcripts, text only)

Scenario files live in `data/fixtures/calls/<stem>.json`, in the same format as s1–s6. The schema is
`{script_id, description, segments: [{call_id, speaker: call_taker|caller, text, t_start, t_end}]}`, with each call's
clock starting at 0. The facts a good agent should extract are in `data/fixtures/calls/expected/<stem>.json`. They sit
one folder down because the API lists every `calls/*.json` file as a script.

All voices are synthetic: invented first names, no phone numbers, no emergency numbers.

| Stem | Building | Calls | What it tests |
|---|---|---|---|
| `s6_fairdale` | 9660 Fairdale Lane, hero `209217448` (scan) | 2 | Floor contradiction for the children (loft upstairs vs a bedroom downstairs); people at floor level and in a room (the office); gas at the separate garage |
| `s7_fairhope_oak` | 18107 Fairhope Oak Street, hero `214905148` (scan) | 3 | Three callers about one garage fire. "Over the kitchen" and "next to the bonus room" resolve to one bedroom through plan relations; "above the garage" (6 rooms) and "front left" stay at floor level |
| `s8_playa_lucia` | 13722 Playa Lucia Court, hero `199958956` (scan, one floor) | 1 | "The room next to the kitchen" matches 7 rooms, so no room is picked; open questions about the exit and the dog |
| `s9_nwtc` | Northwest Trade Center, 9700 Tanner Rd, CoStar `23296` (no scan) | 2 | People count contradiction (3 vs two at most); floor-level locations only; a hazard reported by the caller (forklift batteries) |
| `s10_wilshire` | Wilshire Tower, fixture `fake_wilshire_tower` | 6 | Five ways to say floor 6 (sixth floor, floor six, six up counting the restaurant, right under the roof, at the top) should give one value; call 6 is another incident (a car fire) |

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

`tests/test_call_fixtures.py` checks the structure of every scenario, that the new ones load like the old ones, that
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
