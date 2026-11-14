# Agents API: driving the orchestrator from FastAPI

`services/agents/orchestrator.py` exposes three functions. One incident = one persistent agent
thread; you call them in this order:

```
start_incident(incident_id)              -> pauses, returns candidates
resume_incident(incident_id, building_id) -> brief_v1.json
update_incident(incident_id, notes)       -> brief_v2.json, brief_v3.json, ...
```

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

## `start_incident(incident_id, workspace_root=None, on_todos=None, use_cache=True) -> RunResult`

Starts the agent. It writes its plan, then pauses at the `confirm_building` gate.
`use_cache=False` makes this incident ignore prefetched building data (see Prefetch below).
Leave `workspace_root` as `None` (MongoDB or `workspace/`); a path forces local files (tests).

- Raises `ValueError` if the incident has no candidate buildings yet.
- Returns `status="awaiting_confirmation"`, `candidates` (best match first), `brief=None`.
- Clears earlier output for this incident (briefs, critiques, layers, dossier, notes, todos), then writes:
  - `candidates.json`: list of `BuildingCandidate` (`building_id`, `name`, `address`, `score`)
  - `todos.json`: `Progress` with `status="awaiting_confirmation"`
- Takes about 5 s.

## `resume_incident(incident_id, building_id) -> RunResult`

The dispatcher's answer. Maps to `POST /api/incidents/{id}/confirm-location`.

- `building_id` must be one of the candidates' `building_id`s, else `ValueError`.
  Pass `None` for "none of these": returns `status="rejected"`, no brief is written.
- Raises `ValueError` if the incident is not awaiting confirmation or has no session.
- Returns `status="briefed"`, `version=1`, `brief` (a valid `Brief`).
- Writes:
  - `incident.json`: sets `confirmed_building_id` and `status="confirmed"`
  - `layers/costar.json`, `layers/matterport.json`, `layers/vexcel.json`: one per data layer, pulled
    by three subagents running in parallel. Each subagent's short summary is saved by code as
    `notes/<layer>_summary.md`.
  - `dossier.json`: the three layers merged in code
  - If the building was prefetched less than 24 h ago, the layers and notes are copied from
    the prefetch cache instead and the three subagents do not run.
  - `brief_v1.json`, `critique_v1.json`, `notes/strategy_reasoning.md`
  - `todos.json`: ends with `status="done"`
- Takes roughly 34-40 s cold, 26-29 s when the building was prefetched.

Fusion's record is saved as `incident.json` too (`save_incident`). So that a later save does not
drop the confirmation, tell your `FusionEngine` too: `engine.confirm(building_id)` before or right
after `resume_incident`.
(`update_incident` restores a dropped confirmation and logs a warning, but do not rely on it.)

## `update_incident(incident_id, notes) -> RunResult`

New information after a brief. `notes` is the list of change notes that `FusionEngine.ingest(segment)`
returns; call `save_incident(...)` first, then this. Skip the call when `notes` is empty.

- Raises `ValueError` if there is no brief yet (not confirmed, or rejected) or no session.
- Same agent thread; the data layers and dossier are reused. Only the strategy and critic steps run again.
- Returns `status="briefed"`, `version=N`, `brief` with `changes_since_last` filled in by a
  deterministic diff against the previous version, e.g. `"Floor contradicted: 3 vs 4 (Call 2)"`.
- Writes `brief_vN.json`, `critique_vN.json`, `notes/strategy_reasoning_vN.md`, `todos.json`.
  Earlier versions stay in the workspace.
- Takes roughly 26-35 s.

## `RunResult`

| Field | Meaning |
| --- | --- |
| `incident_id` | |
| `status` | `"awaiting_confirmation"`, `"briefed"` or `"rejected"` |
| `version` | brief version this call worked on |
| `brief` | `Brief` when `status == "briefed"`, else `None` |
| `candidates` | `list[BuildingCandidate]` |
| `todos` | final `list[TodoItem]` (`content`, `status`) |
| `critique` | `{"brief_id", "verdict": "pass" or "revise", "issues", "checked_at"}` or `None` |
| `fallback_reason` | set when the brief is facts-only (see below) |
| `seconds` | wall clock of this call |
| `trace_run_id` | LangSmith run id of this call; `None` when tracing is off |

In the `Brief`: `considerations` (0-5, each "Consider ...", with `evidence_ids` that resolve in
`evidence`), `changes_since_last`, `gaps`, and `verifier_rejections` (how many draft considerations
the code checks and the critic rejected on the way to this version).

Each consideration has `cross_layer: bool`: true when its evidence comes from two or more of the
CoStar / Matterport / Vexcel layers (`evidence[id].source`). Every brief with considerations has at
least one; highlight these in the UI.

`brief.freshness` has `prefetched: bool` and `prefetched_at` (ISO time or `null`) next to
`scan_age_days` and `costar_as_of`: true when the data layers came from the prefetch cache rather than
being pulled for this incident.

Building data comes through the adapters in `services/agents/providers.py`. `DATA_MODE=fixture`
(default) reads `data/fixtures/<building_id>/{costar,matterport,vexcel}.json`; `DATA_MODE=live` is
not implemented yet and logs "live adapter not configured; using fixtures".

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
# status: "running" | "awaiting_confirmation" | "done" | "rejected"
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
