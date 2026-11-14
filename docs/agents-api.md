# Agents API: driving the orchestrator from FastAPI

`services/agents/orchestrator.py` exposes four functions. One incident = one persistent agent
thread; you call them in this order:

```
start_incident(incident_id)               -> pauses, returns candidates
resume_incident(incident_id, building_id) -> brief_v1.json
update_incident(incident_id, notes)       -> brief_v2.json, brief_v3.json, ...
review_brief(incident_id, drop=[...])     -> only when a call above returned "awaiting_approval"
feedback(incident_id, item_id, verdict)   -> any time after a brief; remembered for the building
```

**Always check `RunResult.status` after `resume_incident` and `update_incident`.** `"briefed"` means
the brief is published. `"awaiting_approval"` means the brief has a low-confidence consideration and
is NOT published yet: show it to the dispatcher and call `review_brief`.

Working example: `scripts/run_incident.py`.

## Storage

Two databases, each optional (unset = the old local behaviour):

| What | Where | Env |
| --- | --- | --- |
| The incident's working documents (the files listed below: `incident.json`, `brief_vN.json`, `todos.json`, ...) and the prefetch cache | MongoDB Atlas, collection `workspace_files`, one document per file | `MONGODB_URI`, `MONGODB_DB` (default `flamescout`) |
| Incidents, facts, call segments, published briefs, timings, and the agent threads (LangGraph `PostgresSaver`) | Postgres on Tiger Data | `TIGER_DATABASE_URL` |

- **Read documents through the workspace, not the disk:**
  `from services.agents.workspace import open_workspace` then
  `open_workspace(incident_id).read("brief_v1.json")` (also `.list()`, `.exists()`). It is MongoDB
  when `MONGODB_URI` is set, else `workspace/<incident_id>/`. Every path in this guide is relative to it.
- In MongoDB, each `*.json` document also carries its parsed content in `doc`, so it can be queried
  in Atlas (e.g. `{"ns": ["incidents", "inc_s2"], "doc.version": 2}`).
- Tiger tables: `incidents`, `facts`, `call_segments` (hypertable), `briefs`, `pipeline_events`
  (hypertable), continuous aggregate `stage_seconds_hourly`, view `time_to_brief`.
  `db.time_to_brief_stats()` gives P50 / P95 (cold vs prefetched) for `/api/metrics/time-to-brief`.
  Database writes are best effort: a database error is logged and never stops a brief.

## Sessions

- **Import and call these functions directly from the FastAPI process.** Do not run them in a
  subprocess per request.
- **With `TIGER_DATABASE_URL` set, an incident survives a server restart**, including one paused for
  confirmation: the next `resume_incident` / `update_incident` rebuilds the session from Tiger and
  continues the same agent thread (`on_todos` must be passed again by starting over; it is not stored).
- **Without it**, sessions live in the process: a restart loses them, and `resume_incident` /
  `update_incident` raise `ValueError("No session ...")`; call `start_incident` again.
- Run uvicorn with one worker (`--workers 1`) and no `--reload` during the demo.
- All three functions are blocking and take seconds (see timings below). Call them from a sync route
  (`def`, not `async def`) or with `run_in_threadpool`, never directly inside an `async def` handler.
- Do not call two of them for the same incident at the same time.

## Before you call anything

The incident's `incident.json` must exist, and callers must have given an address or building name,
otherwise there are no candidates. After every `FusionEngine.ingest`, save fusion's record with

```python
save_incident(engine.record, new_segments=[segment])   # from services.agents.orchestrator
```

It writes `incident.json` to the workspace and records the segment and the facts in Tiger. Never
write `incident.json` yourself. Use a new `incident_id` for each new incident: `start_incident`
clears the earlier output and brief rows of an id it has seen before.

## `start_incident(incident_id, workspace_root=None, on_todos=None, on_overlay=None, use_cache=True) -> RunResult`

Starts the agent. It writes its plan, then pauses at the `confirm_building` gate.
`use_cache=False` makes this incident ignore prefetched building data (see Prefetch below).
Leave `workspace_root` as `None` (MongoDB or `workspace/`); a path forces local files (tests).

- Raises `ValueError` if the incident has no candidate buildings yet.
- Returns `status="awaiting_confirmation"`, `candidates` (best match first), `brief=None`.
- Clears earlier output for this incident (briefs, critiques, layers, dossier, notes, todos), then writes:
  - `candidates.json`: list of `BuildingCandidate` (fields below)
  - `todos.json`: `Progress` with `status="awaiting_confirmation"`
- Takes about 5 s.

### `BuildingCandidate`

The same fields as the rules pipeline's `LocationCandidate` (`GET /api/incidents/{id}/candidates`);
in the schema it is a subclass of it, so one UI type renders both.

| Field | Meaning |
| --- | --- |
| `building_id` | pass it to `resume_incident` |
| `name`, `address` | from the CoStar record |
| `coordinates` | `{"lat": .., "lon": ..}` from the CoStar record; `{}` if it has none |
| `score` | 0-1: how well the best thing a caller said matches the address or the name |
| `match_reasons` | why it matched, one line per thing a caller said: `"address exact match"`, `"address partial match: 1200 Wilshire Boulevard (0.50)"`, `"name match: Wilshire Tower"`, `"name partial match: Wilshire Tower (0.33)"` |
| `scan_available` | `true` / `false`: whether the Matterport adapter has a model for it; `null` if the lookup failed |

All of it is computed in code (`locator.find_candidates`), no model call. The two pipelines word
`match_reasons` differently (the rules pipeline writes `"name 'Wilshire Tower' matched (0.92)"`);
show them as text, do not parse them. `scan_available` is a hint for the dispatcher: the brief
decides from the Matterport layer it pulls after confirmation.

## `resume_incident(incident_id, building_id) -> RunResult`

The dispatcher's answer. Maps to `POST /api/incidents/{id}/confirm-location`.

- `building_id` must be one of the candidates' `building_id`s, else `ValueError`.
  Pass `None` for "none of these": returns `status="rejected"`, no brief is written.
- Raises `ValueError` if the incident is not awaiting confirmation or has no session.
- Returns `status="briefed"`, `version=1`, `brief` (a valid, published `Brief`), or
  `status="awaiting_approval"` (see `review_brief`).
- Writes:
  - `incident.json`: sets `confirmed_building_id` and `status="confirmed"`
  - `layers/costar.json`, `layers/matterport.json`, `layers/vexcel.json`: one per data layer, pulled
    by three subagents running in parallel. Each subagent's short summary is saved by code as
    `notes/<layer>_summary.md`.
  - `dossier.json`: the three layers merged in code
  - If the building was prefetched less than 24 h ago, the layers and notes are copied from
    the prefetch cache instead and the three subagents do not run.
  - `overlay_v1.json`: the incident overlay for the 3D viewer (see "Incident overlay" below), by an
    `incident_mapper` subagent running in parallel with the three layer subagents
  - `brief_v1.json`, `critique_v1.json`, `notes/strategy_reasoning.md`
  - `copilot_questions.json` if callers contradict each other on the floor or on trapped occupants (see below)
  - `todos.json`: ends with `status="done"`, or `"awaiting_approval"`
- Takes roughly 40-55 s cold, less when the building was prefetched, up to about 60 s for a building
  with no interior scan (one extra planning step). A stalled model call adds up to about 12 s before its retry.

The plan in `todos.json` depends on the situation. For a building with no interior scan the
"Matterport interior scan" todo becomes "No interior scan on file: interior steps skipped", an
"Exterior entries from aerial imagery" todo is added, and the brief's `gaps` start with
"No interior scan on file". Entries then come from aerial imagery with confidence below 0.5, so a
consideration that uses one puts the brief in `awaiting_approval`.

Fusion's record is saved as `incident.json` too (`save_incident`). So that a later save does not
drop the confirmation, tell your `FusionEngine` too: `engine.confirm(building_id)` before or right
after `resume_incident`.
(`update_incident` restores a dropped confirmation and logs a warning, but do not rely on it.)

## `update_incident(incident_id, notes) -> RunResult`

New information after a brief. `notes` is the list of change notes that `FusionEngine.ingest(segment)`
returns; call `save_incident(...)` first, then this. Skip the call when `notes` is empty.

- Raises `ValueError` if there is no brief yet (not confirmed, or rejected), no session, or a brief
  is still `awaiting_approval` (call `review_brief` first).
- Same agent thread; the data layers and dossier are reused. Only the strategy and critic steps run again.
- Returns `status="briefed"` or `"awaiting_approval"`, `version=N`, `brief` with `changes_since_last`
  filled in by a deterministic diff against the previous version, e.g.
  `"Floor contradicted: 3 vs 4 (Call 2)"`.
- Writes `overlay_vN.json` (the mapper runs first: the strategy reads the new overlay),
  `brief_vN.json`, `critique_vN.json`, `notes/strategy_reasoning_vN.md`, `todos.json`, and
  `copilot_questions.json` when the new information contains a life-safety contradiction.
  Earlier versions stay in the workspace.
- Takes roughly 40-45 s (the overlay update adds one subagent step, about 15 s).

## `review_brief(incident_id, drop=()) -> RunResult`

The dispatcher's answer when a brief is held. A brief is held when any consideration has
`confidence < 0.5`: the agent pauses before publishing (`interrupt_on` on its `publish_brief` tool).

- While held: `RunResult.status == "awaiting_approval"`, `RunResult.needs_review` lists the
  low-confidence consideration ids, `RunResult.brief` is the draft with `published == false`,
  and `todos.json` has `status="awaiting_approval"`. **Do not send a brief with `published == false`
  to the crew.**
- `review_brief(incident_id)` approves: the brief is published as it is.
- `review_brief(incident_id, drop=["c1"])` publishes it without those considerations. Any
  consideration id of the brief can be dropped, not only the low-confidence ones. The rest are
  re-ranked, ids stay the same, and `gaps` gets "Dispatcher dropped N low-confidence
  consideration(s): c1". Dropping can remove the brief's only cross-layer consideration.
- Raises `ValueError` if nothing is awaiting approval, or an id is not in the brief.
- Returns `status="briefed"` with the published brief. Takes 1-5 s.
- Works after a restart with Tiger, like the other functions.

## Crew feedback and building memory: `feedback(...)`

```python
from services.agents.memory import feedback

fb = feedback(incident_id, item_id, verdict, note=None, role="officer")   # -> CrewFeedback
```

The officer marks a fact or marker of a brief as wrong, outdated or confirmed. It is stored for the
**building**, so it applies to the next brief version of this incident and to every later incident
at the same building. Call it from `POST /api/incidents/{id}/feedback`.

- `item_id`: a marker id (`brief.markers[].id`, e.g. `"m_stair_b"`) or a fact key (a key of
  `brief.size_up`, or `brief.hazards[].key`, e.g. `"building.stories"`), taken from the incident's
  latest brief. An item already in the building's memory is accepted too, so a marker that was
  left out as wrong can later be confirmed.
- `verdict`: `"wrong"`, `"outdated"` or `"confirmed"`. The returned `CrewFeedback.verdict` uses the
  schema's word `"correct"` for confirmed; `"correct"` is accepted as input as well.
- `note`: free text, shown in the brief ("bricked up at the alley door").
- Raises `ValueError` for an unknown verdict, an incident with no confirmed building or no brief
  yet, or an `item_id` that is in neither the latest brief nor the building's memory.
- Returns the stored `CrewFeedback` (`id`, `incident_id`, `building_id`, `fact_key` = the item id,
  `verdict`, `note`, `role`, `created_at`). No model call; needs no live session; takes milliseconds.

What it writes: the building's memory, in MongoDB (namespace `["memories"]`, keys
`/<building_id>/feedback.json` and `/<building_id>/crew_notes.md`) when `MONGODB_URI` is set, else
`data/memories/<building_id>/` (gitignored). The orchestrator mounts it read-only at `/memories/`.
Read it with `memory.building_memory(building_id)` (newest record per item) or
`memory.records(building_id)` (everything).

What the next brief at that building looks like (`resume_incident` or `update_incident`):

- `todos.json` gets "Read crew memory for this building", and the strategy subagent reads
  `/memories/<building_id>/crew_notes.md`.
- `brief.size_up["crew.<item_id>"]`: a fact with `status: "reported"` and a value such as
  `"Crew reported: Stair B is wrong: bricked up at the alley door (2026-11-14)"`. Its
  `evidence_ids` resolve to an evidence item with `kind: "crew_feedback"`, `source: "crew"`.
- `brief.crew_notes`: the `CrewFeedback` records themselves (newest per item).
- **A marker marked wrong is gone**: not in `brief.markers`, not in `dossier.json`, and no
  consideration may name it (a draft that does is rejected and counted in `verifier_rejections`).
  The raw `layers/*.json` still contain it. A later `"confirmed"` on the same item brings it back.
- A *fact* marked wrong stays in `size_up` with `status: "contradicted"`.
- `"outdated"` and `"confirmed"` add the "Crew reported" fact only; they do not change the item.
- If the feedback arrives between two versions of the same incident, the next version's
  `changes_since_last` contains the "Crew reported: ..." line.

This is separate from the rules pipeline's `add_feedback` (`feedback.jsonl` and
`_buildings/<building_id>/feedback.jsonl` in the JSON workspace): the orchestrator does not read
those files, and `feedback()` does not write them. While both pipelines exist, call both.

## `copilot_questions.json`: questions the orchestrator plans for the dispatcher

When callers contradict each other on `incident.floor` or `people.trapped` and the dispatcher has
not resolved it, the workspace gets `copilot_questions.json`: a list of `CopilotSuggestion` (the
same schema `/api/incidents/{id}/copilot` returns):

```json
[{"id": "inc_s2--incident_floor", "incident_id": "inc_s2", "field": "incident.floor",
  "question": "Callers disagree on floor (3 vs 4). Ask: which floor are the flames or smoke on?",
  "reason": "contradiction", "status": "open"}]
```

- **This is not `copilot.json`.** `copilot.json` is the API's own `{suggestion_id: status}` map
  (`services/api/store.py`); the orchestrator never reads or writes it. Ids are built with the same
  `copilot.suggestion_id(incident_id, field)`, so a status in `copilot.json` can be applied to these.
- The API already derives a suggestion for every contradiction (`copilot.suggestions`). What this
  file adds is the wording for the two life-safety keys, and that the question is a planned step:
  it appears as a todo ("Ask dispatcher: floor contradiction") before the brief is finalized.
  It does not hold the brief back.
- The wording is built in code from the contradiction record; the values are the callers' own.
- Read it with `copilot.read_questions(open_workspace(incident_id))`. A later run keeps an entry's
  `status` as long as its wording is unchanged.
- After the dispatcher resolves it (`FusionEngine.resolve(key, value)` + `save_incident`), the
  question is not asked again, but the old entry stays in the file: filter on `status`.

## Incident overlay: `overlay_vN.json` and `overlay.updated`

The contract is Bruno's `docs/INCIDENT_OVERLAY_SPEC.md`; the model is `IncidentOverlay` in
`packages/schemas` (in `schema.json`). One overlay per brief version, **`overlay.version` equals the
brief version**, written to the workspace as `overlay_vN.json` and returned as `RunResult.overlay`.

**For the WebSocket: send it early, from `on_overlay`.** Pass `on_overlay=callback` to
`start_incident` (it stays attached for the incident's later calls, like `on_todos`). It is called
with the `IncidentOverlay` the moment `overlay_vN.json` is written: as soon as the mapper's submission
is accepted, before the strategy and the critic run, so the viewer has it 15-30 s before the brief.
`N` is the brief version still being produced. In it, emit `overlay.updated` with
`{incident_id, ts, trace_id, version, overlay}`, where `overlay` is
`ov.model_dump(mode="json", exclude_none=True)` (the same JSON as `overlay_vN.json`).
`"overlay.updated"` is in `EventType`. It also fires for the empty fallback overlay, so every brief
version gets one. Like `on_todos`, it runs on the orchestrator's thread: keep it fast, hand the
payload to the event loop. `RunResult.overlay` and `overlay_vN.json` still have it afterwards.
The web app forwards it to the viewer iframe with `postMessage({type: 'setIncident', overlay}, viewerOrigin)`.

- **What is in it:** occupants, fire, hazards and blocked elements the callers reported, placed on plan
  ids at the precision the words support (room / anchor / floor / unknown), contradiction groups
  (never resolved by the agent), objectives (rescue / recon, with priority), a few considerations, and
  `changes_since_last` (a code diff against the previous version). `routes` is always `[]`: Bruno's
  planner in the viewer fills them.
- **Evidence:** every `evidence_ids` entry is a transcript span id `"<call_id>:<t_start>-<t_end>"`
  (e.g. `"c1:42.0-51.5"`). Each one also appears in the brief of the same version, in `brief.evidence`
  (kind `transcript_span`, with the caller's words in `locator.text`), so the evidence drawer resolves it.
- **Brief considerations point at it:** `Consideration.refs` lists overlay ids (occupant, fire, hazard
  ids, contradiction group ids, objective ids) of the same version. Clicking a brief consideration can
  highlight those in the viewer.
- **Checked before it is written:** Bruno's `validate_overlay.py` plus ours: evidence must be caller
  spans, no `verified` from a caller, contradiction candidates come from different calls, a room
  location only when the words name that kind of room and the plan has only one such room on that
  floor, plan ids only. Rejected submissions go back to the mapper (two more tries);
  `RunResult.overlay_rejections` counts them.
- **If the mapper fails** (out of tries, or the run fails), the overlay of that version is valid but
  empty, with `changes_since_last: ["Overlay not available: <reason>"]`. The viewer keeps its version
  in step and shows nothing new.
- **Plan index:** read from `data/raw/<building_id>/derived/plan_index.json` (sponsor data, gitignored,
  never committed). Missing means no interior scan: only `floor` (with `floor_reported`) and `unknown`
  locations, no routes. That is the case for every fixture building today, scanned or not, except
  when you copy a plan index in.
- **Transcript:** `save_incident(record, new_segments)` now also appends the segments to
  `transcript.json` in the workspace; that is what the mapper cites. Always pass the new segments.

## `RunResult`

| Field | Meaning |
| --- | --- |
| `incident_id` | |
| `status` | `"awaiting_confirmation"`, `"awaiting_approval"`, `"briefed"` or `"rejected"` |
| `version` | brief version this call worked on |
| `brief` | the published `Brief` when `"briefed"`; the unpublished draft when `"awaiting_approval"`; else `None` |
| `needs_review` | consideration ids with confidence below 0.5 (only when `"awaiting_approval"`) |
| `candidates` | `list[BuildingCandidate]` |
| `todos` | final `list[TodoItem]` (`content`, `status`) |
| `critique` | `{"brief_id", "verdict": "pass" or "revise", "issues", "note_flags", "checked_at"}` or `None` |
| `fallback_reason` | set when the brief is facts-only (see below) |
| `seconds` | wall clock of this call |
| `trace_run_id` | LangSmith run id of this call; `None` when tracing is off |
| `model_stalls` | model calls in this call that got no answer within the 12 s client timeout |
| `model_retries` | retries made in this call (each stall or transient API error is retried once) |
| `overlay` | the `IncidentOverlay` of this version (`overlay_vN.json`); `None` while there is no brief |
| `overlay_rejections` | incident-mapper submissions refused by the checks for this version |
| `overlay_seconds` | seconds into this call when the overlay was written and `on_overlay` fired |

In the `Brief`: `considerations` (0-5, each "Consider ...", with `evidence_ids` that resolve in
`evidence`), `changes_since_last`, `gaps`, and `verifier_rejections` (how many draft considerations
the code checks and the critic rejected on the way to this version).

`Brief.published: bool` is true once the brief may go to the crew: immediately when no consideration
is below 0.5 confidence, otherwise after `review_brief`. Tiger's `briefs` table only gets published briefs.

`notes/<layer>_summary.md` are **unverified agent notes** and start with a line saying so. The brief
is never built from them. On the first brief the critic compares each note with its layer JSON and
records claims the data does not support in `critique.note_flags` (`[{"layer", "claim"}]`); each
flagged claim is also appended to the note as a "> Critic flag ..." line. Treat the notes as
commentary for debugging and the demo, not as data.

The strategy subagent loads two skills on demand from `skills/` (`occupancy-hazards`,
`size-up-format`); edit those `SKILL.md` files to change the hazard table or the house style.

Each consideration also has `refs`: ids in the incident overlay of the same version (see above).

Each consideration has `cross_layer: bool`: true when its evidence comes from two or more of the
CoStar / Matterport / Vexcel layers (`evidence[id].source`). Every brief with considerations has at
least one; highlight these in the UI.

`brief.freshness` has `prefetched: bool` and `prefetched_at` (ISO time or `null`) next to
`scan_age_days` and `costar_as_of`: true when the data layers came from the prefetch cache rather than
being pulled for this incident.

Building data comes through the adapters in `services/agents/providers.py`. For the hero buildings
it is the stored real API responses in `data/raw/<id>/` (gitignored sponsor data; `deploy.sh` copies
it to the server): `209217448` 9660 Fairdale Lane, `214905148` 18107 Fairhope Oak Street,
`199958956` 13722 Playa Lucia Court, `23296` Northwest Trade Center, 9700 Tanner Rd (no interior
scan). Every other id comes from `data/fixtures/`. No vendor API is called; `DATA_MODE=live` logs that
this is not configured.

`brief.data_contradictions` (and `dossier.contradictions`): the data layers disagree, today the
listing (CoStar) and the scan (Matterport) on bedrooms, bathrooms (full + half) or floors. Each has
`key`, `message` (e.g. `"Listing: 4 bedrooms; scan: 5. Verify occupant count."`), `values` (one per
layer, with `evidence_ids`) and `status: "contradicted"`. The listing fact in `size_up` is marked
`contradicted` and the message is also in `gaps`. Code never picks a side.

`building.sprinklered` is `true` only from a fire-protection field (a commercial record's
`hasSprinklers`, or "Fire Sprinkler..." under a listing's Home Security). A "Sprinkler System" under
a listing's Lot Details is lawn irrigation: the fact stays `unverified`, and its value says why.

Every orchestrator and subagent model call has a 12 s client timeout and one automatic retry
(`services/agents/stalls.py`). Each stall and retry is logged at WARNING on the logger
`services.agents.stalls` with the agent's name ("Model stall: critic got no answer in 10.5 s; retrying
once"), and counted in `RunResult.model_stalls` / `model_retries` and in Tiger's `pipeline_events`
detail. If the retry stalls too, that agent's step fails and the usual fallbacks apply.

**The functions do not raise when the agent or the model fails.** The crew still gets a brief with
every deterministic fact and no considerations; `fallback_reason` says why and `gaps[0]` starts with
"Considerations withheld". Show that brief as it is.

## Prefetch: `prefetch_building(building_id) -> PrefetchResult`

Runs the three layer subagents ahead of any incident and caches what they pull (PRD F13). Same thing
from the shell: `python scripts/prefetch.py <building_id>`. Takes about 10 s.

- Writes the building's cache (MongoDB namespace `["cache", <building_id>]`, or the gitignored
  `data/cache/<building_id>/`): `cache.json` (`building_id`, `prefetched_at`),
  `layers/{costar,matterport,vexcel}.json`, `notes/<layer>_summary.md`.
- Raises `FileNotFoundError` for a building with no data, before any model call.
- Returns `building_id`, `prefetched_at`, `workspace` (the cache), `notes` (layer -> summary) and `seconds`.
- For 24 h, `resume_incident` at that building reuses the cache: no layer subagents, about 8 s faster,
  and the brief says so in `freshness.prefetched`. Older caches are ignored, not deleted.
- Blocking, like the other functions. It does not need or touch an incident session, so it can run
  at server start-up for the hero buildings or from a cron job.

## `on_todos`: live progress strip

Pass `on_todos=callback` to `start_incident`; it stays attached to the incident for later calls.
It is called with a `Progress` every time the agent's plan or status changes:

```python
Progress(incident_id, status, version, candidates, todos, updated_at, trace_run_id)
# status: "running" | "awaiting_confirmation" | "awaiting_approval" | "done" | "rejected"
# todos:  [TodoItem(content="Load building dossier", status="pending" | "in_progress" | "completed"), ...]
```

- It runs on the thread that called `start_incident` / `resume_incident` / `update_incident`, in the
  middle of the run. Keep it fast and never raise from it. To push over a WebSocket, hand the
  payload to the event loop (`asyncio.run_coroutine_threadsafe(...)` or a queue).
- The same `Progress` is written to the workspace's `todos.json` on every change, so polling
  `open_workspace(incident_id).read("todos.json")` works too.

## Sketch

```python
from services.agents.orchestrator import save_incident, start_incident, resume_incident, update_incident

@app.post("/api/incidents/{incident_id}/start")
def start(incident_id: str):
    r = start_incident(incident_id, on_todos=lambda p: push(incident_id, "dossier.progress", p))
    return {"status": r.status, "candidates": r.candidates}

@app.post("/api/incidents/{incident_id}/confirm-location")
def confirm(incident_id: str, body: ConfirmBody):
    engines[incident_id].confirm(body.building_id)
    r = resume_incident(incident_id, body.building_id)
    return r.brief

# after fusion ingests a segment and returns change notes:
notes = engines[incident_id].ingest(segment)
save_incident(engines[incident_id].record, new_segments=[segment])
if notes and has_brief(incident_id):
    r = update_incident(incident_id, notes)
```
