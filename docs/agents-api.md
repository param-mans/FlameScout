# Agents API: driving the orchestrator from FastAPI

`services/agents/orchestrator.py` exposes three functions. One incident = one persistent agent
thread; you call them in this order:

```
start_incident(incident_id)              -> pauses, returns candidates
resume_incident(incident_id, building_id) -> brief_v1.json
update_incident(incident_id, notes)       -> brief_v2.json, brief_v3.json, ...
```

Working example: `scripts/run_incident.py`.

## Sessions are in-process

- The agent's state lives in an `InMemorySaver` inside the Python process. **Import and call these
  functions directly from the FastAPI process.** Do not run them in a separate worker process or a
  subprocess per request: the second process would not see the session.
- **A server restart loses every session**, including incidents paused for confirmation. After a
  restart, `resume_incident` / `update_incident` raise `ValueError("No session ...")`; call
  `start_incident` again (it starts the incident's thread clean and re-plans).
- Run uvicorn with one worker (`--workers 1`) and no `--reload` during the demo, for the same reason.
- All three functions are blocking and take seconds (see timings below). Call them from a sync route
  (`def`, not `async def`) or with `run_in_threadpool`, never directly inside an `async def` handler.
- Do not call two of them for the same incident at the same time.

## Before you call anything

`workspace/<incident_id>/incident.json` must exist (fusion writes it: `FusionEngine.record`), and
callers must have given an address or building name, otherwise there are no candidates.

## `start_incident(incident_id, workspace_root=WORKSPACE, on_todos=None) -> RunResult`

Starts the agent. It writes its plan, then pauses at the `confirm_building` gate.

- Raises `ValueError` if the incident has no candidate buildings yet.
- Returns `status="awaiting_confirmation"`, `candidates` (best match first), `brief=None`.
- Clears earlier output for this incident (briefs, critiques, layers, dossier, notes, todos), then writes:
  - `candidates.json`: list of `BuildingCandidate` (`building_id`, `name`, `address`, `score`)
  - `todos.json`: `Progress` with `status="awaiting_confirmation"`
- Takes about 4 s.

## `resume_incident(incident_id, building_id) -> RunResult`

The dispatcher's answer. Maps to `POST /api/incidents/{id}/confirm-location`.

- `building_id` must be one of the candidates' `building_id`s, else `ValueError`.
  Pass `None` for "none of these": returns `status="rejected"`, no brief is written.
- Raises `ValueError` if the incident is not awaiting confirmation or has no session.
- Returns `status="briefed"`, `version=1`, `brief` (a valid `Brief`).
- Writes:
  - `incident.json`: sets `confirmed_building_id` and `status="confirmed"`
  - `layers/costar.json`, `layers/matterport.json`, `layers/vexcel.json`: one per data layer, pulled
    by three subagents running in parallel, each with a note `notes/<layer>_summary.md`
  - `dossier.json`: the three layers merged in code
  - `brief_v1.json`, `critique_v1.json`, `notes/strategy_reasoning.md`
  - `todos.json`: ends with `status="done"`
- Takes roughly 40-50 s.

Fusion also writes `incident.json`. So that a later save does not drop the confirmation, tell your
`FusionEngine` too: `engine.confirm(building_id)` before or right after `resume_incident`.
(`update_incident` restores a dropped confirmation and logs a warning, but do not rely on it.)

## `update_incident(incident_id, notes) -> RunResult`

New information after a brief. `notes` is the list of change notes that `FusionEngine.ingest(segment)`
returns; save the updated `incident.json` first, then call this. Skip the call when `notes` is empty.

- Raises `ValueError` if there is no brief yet (not confirmed, or rejected) or no session.
- Same agent thread; the data layers and dossier are reused. Only the strategy and critic steps run again.
- Returns `status="briefed"`, `version=N`, `brief` with `changes_since_last` filled in by a
  deterministic diff against the previous version, e.g. `"Floor contradicted: 3 vs 4 (Call 2)"`.
- Writes `brief_vN.json`, `critique_vN.json`, `notes/strategy_reasoning_vN.md`, `todos.json`.
  Earlier versions stay on disk.
- Takes roughly 25 s.

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

Building data comes through the adapters in `services/agents/providers.py`. `DATA_MODE=fixture`
(default) reads `data/fixtures/<building_id>/{costar,matterport,vexcel}.json`; `DATA_MODE=live` is
not implemented yet and logs "live adapter not configured; using fixtures".

**The functions do not raise when the agent or the model fails.** The crew still gets a brief with
every deterministic fact and no considerations; `fallback_reason` says why and `gaps[0]` starts with
"Considerations withheld". Show that brief as it is.

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
- The same `Progress` is written to `workspace/<incident_id>/todos.json` on every change, so polling
  that file works too.

## Sketch

```python
from services.agents.orchestrator import start_incident, resume_incident, update_incident

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
if notes and has_brief(incident_id):
    r = update_incident(incident_id, notes)
```
