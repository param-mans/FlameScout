# FlameScout

**FlameScout turns live fire-dispatch calls into a building-specific size-up on the crew's tablet before the engine arrives.**

HackPrinceton Fall 2026 · main track: Healthcare (public safety and emergency response).
**Simulation only: nothing here calls a phone number or connects to a 911 line.** All callers are synthetic. Considerations are advisory ("Consider ..."), never orders.

## Contents

1. [The problem and the idea](#1-the-problem-and-the-idea)
2. [What you see](#2-what-you-see)
3. [Design rules](#3-design-rules)
4. [How an incident flows, end to end](#4-how-an-incident-flows-end-to-end)
5. [Architecture](#5-architecture)
6. [Deep dive: the pieces](#6-deep-dive-the-pieces)
7. [API reference](#7-api-reference)
8. [The web app](#8-the-web-app)
9. [Repository layout](#9-repository-layout)
10. [Setup and running](#10-setup-and-running)
11. [Configuration reference](#11-configuration-reference)
12. [Deployment (Vultr)](#12-deployment-vultr)
13. [Testing](#13-testing)
14. [Troubleshooting](#14-troubleshooting)
15. [Measured performance](#15-measured-performance)
16. [Sponsor technologies](#16-sponsor-technologies)
17. [Client consultation (Peri Forward Deployed Challenge)](#17-client-consultation-peri-forward-deployed-challenge)
18. [Known limits and roadmap](#18-known-limits-and-roadmap)
19. [Documentation index](#19-documentation-index)

---

## 1. The problem and the idea

On arrival, a company officer spends minutes finding entries, stairs, occupancy and hazards. Pre-incident plans cover few buildings and go stale. What callers say is scattered across several calls and often disagrees ("third floor" vs "fourth floor").

FlameScout:

1. **Listens** to dispatch calls (scripted, typed, or a recorded call-in) and fuses every caller into one evolving incident record. Contradictions are flagged and never auto-resolved.
2. **Locates** the building (CoStar property search). The dispatcher confirms it, or it auto-confirms when the match is clear.
3. **Pulls a dossier** from three layers for the same address: **CoStar** (use, size, stories, year built, tenants), **Matterport** (the interior scan: floors, rooms, doors, stairs) and **Vexcel** (aerial views, read once by a vision model; distances computed in code).
4. **Synthesizes a brief**: size-up facts, hazard flags, exposures, up to five ranked advisory considerations, gaps and a data-freshness banner. Every claim carries a source, a confidence and an evidence link, or is marked *unverified*.
5. **Plots routes**: where callers report people, smoke and fire is placed on the 3D and 2D plan, and a deterministic planner draws entry-to-target routes. When no route can be computed, it says so.
6. **Updates live**: each new call produces a new brief version with a "changed since" line.

**Target users:** the company officer en route (the MDT view), the dispatcher on the call (the dispatcher console), and the incident commander (a shared picture with data age and confidence).

## 2. What you see

**Dispatcher console.** A live transcript (calls and speakers), the incident record with a status chip on every fact, contradiction banners with a "Use" button per candidate value, the location confirmation card, the orchestrator's progress strip (its to-do list), and copilot questions (what to ask next, based on what is missing or contradicted). You can also type transcript lines yourself: each caller line shows the facts the model extracted from it, with the quote it relied on. A "Call in" control records a caller's monologue, transcribes it and feeds the sentences in as caller lines.

**MDT (the officer's tablet).** The top bar shows the confirmed address, the incident type, the scan's age, the CoStar record date and the brief version. Below: glanceable readouts, the size-up and hazards, up to five considerations, a gaps strip, and an evidence inspector. Selecting a fact, hazard or marker opens its evidence (source, date, aerial crop) and **Confirm / Mark wrong / Mark outdated** buttons: the crew's verdict is stored per building and applied to the next brief and to later incidents there. The center is a 3D and 2D house viewer with the incident layer: people, fire, hazards and routes. A building without an interior scan shows "No interior scan on file" and the gaps, never invented data.

**Top bar.** A **LIVE** or **MOCK DATA** badge (the app falls back to mock data when the API is down), a scripted-call picker with **Run call**, the Call-in control, and a chip with the measured time to brief (median and p95, read from Tiger Data; facts-only fallbacks are not counted). `?mock=1` forces mock data; `?admin=1` shows demo scaffolding.

## 3. Design rules

These are non-negotiable and are enforced in code and tests, not just in prose.

- **Advisory only.** Wording is "Consider ...", never an order. The critic and the checks reject order words.
- **Evidence-linked.** Every `Fact`, `Marker` and `Consideration` has a status (`verified`, `inferred`, `reported`, `unverified`, `contradicted`), a confidence and `evidence_ids` that resolve in the brief's `evidence` dictionary. Unsupported claims are marked `unverified`.
- **Deterministic first.** Distances, heights, counts, contradictions, place resolution and **routes** are computed in code. Models only extract facts from one utterance, read images, draft considerations and critique. The agent never outputs coordinates, polygons or route geometry.
- **Abstain over guess.** Missing or unverifiable data is shown as a gap.
- **Structured output only.** Every model call returns a Pydantic model; schema errors are retried.
- **Never invent third-party API fields.** Providers sit behind adapters with fixtures; `DATA_MODE=fixture` makes the demo independent of external APIs.
- **Simulation only.** No real emergency number is ever contacted. Secrets live only in `.env` (gitignored).

## 4. How an incident flows, end to end

```
 caller line (scripted | typed | call-in audio -> Gemini transcription)
        |
        v
 1. EXTRACT   one caller utterance -> ExtractedFact list (Gemini, structured output, verbatim quotes;
              a code street-address spotter backs the model up; a keyword stub is the offline fallback)
        |
        v
 2. FUSE      deterministic merge into the IncidentRecord: corroboration, contradictions across calls,
              open questions, copilot suggestions            (plain code, median well under 1 s per line)
        |
        v
 3. LOCATE    location facts -> CoStar candidates (score + reasons) -> dispatcher confirms
              (auto-confirm when one candidate, or best >= 0.8 and 0.2 ahead of the second)
        |
        v
 4. BRIEF     (a) fast path: ONE structured model call writes the overlay and the considerations
                  from the transcript, facts and the three data layers; code places every report on
                  the plan, validates, and publishes. Falls back to (b) if its checks fail twice.
              (b) agent path: a deep agent plans, pulls the layers in parallel, maps reports onto the
                  plan, drafts considerations, and a critic checks them. Pauses for a human if a
                  consideration is below 0.5 confidence.
        |
        v
 5. PUBLISH   brief_vN.json + overlay_vN.json -> WebSocket -> MDT. The route planner in the viewer
              draws routes from the overlay's objectives. New facts after a brief -> version N+1.
```

## 5. Architecture

```
                        ┌────────────────────────── browser (React + Vite) ───────────────────────────┐
                        │  Dispatcher console        MDT view          3D/2D house viewer (iframe)    │
                        │  REST actions + one WebSocket per incident (/ws/incidents/{id}, /ws/calls)  │
                        └───────────────┬─────────────────────────────────────────┬──────────────────┘
                                        │ /api, /ws, /health                      │ /api/house-viewer/*
                                        v                                         v
                    ┌───────────────────────────────────┐           ┌─────────────────────────────┐
                    │  api (FastAPI)  services/api      │           │ house-data (Node)           │
                    │  IncidentService, event bus,      │           │ live Matterport plans,      │
                    │  metrics, audit, transcription    │           │ plan index, Vexcel imagery  │
                    └───────┬───────────────┬───────────┘           └──────────────┬──────────────┘
                            │               │                                      │
              services/agents (code)        │                          Matterport API · Vexcel API
   extract · fusion · locator · dossier ·   │
   brief · rules · overlay · memory · cache │
   fast_path · orchestrator (deepagents)    │
                            │               └──────────────┐
                            v                              v
              Gemini / xAI / OpenAI (llm.py)     Workspace: files or MongoDB Atlas
              LangSmith tracing                  Postgres + TimescaleDB on Tiger Data
```

| Part | Where | Role |
|---|---|---|
| Schemas | `packages/schemas/` | Pydantic models: the contract between agents, API and UI |
| Agents and rules | `services/agents/` | extraction, fusion, locator, dossier, brief, rules, overlay, memory, the deepagents orchestrator |
| API | `services/api/` | FastAPI app: REST, WebSocket, metrics, audit, call-in transcription |
| Web app | `apps/web/` | dispatcher console, MDT, the house viewer, and the Node house-data service |
| Deployment | `deploy/`, `docker-compose.yml`, `Dockerfile` | Compose (api, house-data, web/Caddy) and the Vultr scripts |

## 6. Deep dive: the pieces

### 6.1 Data model (`packages/schemas/flamescout_schemas/__init__.py`)

The shared contract. Python models are exported to JSON Schema (`scripts/export_schemas.py`); the TypeScript mirror is `apps/web/src/types/schemas.ts` (update both when a model changes).

- **Primitives:** `Evidence` (kind: scan frame or floorplan, aerial crop, CoStar record, transcript span, rule output; with source, URI, capture date and a locator such as call id and time span) and `Fact` (key, value, status, confidence, evidence ids, `as_of`).
- **Incident:** `CallSegment` (call id, speaker, text, times), `Contradiction` (key and the differing values with spans; `resolved_by_dispatcher`), `IncidentRecord` (facts, evidence, contradictions, open questions, confirmed building, ready-to-brief flag), `LocationCandidate`/`BuildingCandidate` (score, match reasons, scan available), `CopilotSuggestion`.
- **Brief:** `Marker` (entry, stair, exit, elevator, key box, roof access, FDC, standpipe), `BuildingDossier`, `Consideration` (rank, category, title, rationale, confidence, evidence ids; max five), `Brief` (version, freshness, size-up, markers, hazards, exposures, considerations, `changes_since_last`, gaps, disclaimer, `published`, partial flags).
- **Incident overlay (for the viewer):** `IncidentOverlay` with occupants, fire, hazards, blocked passages, contradictions, objectives (`rescue` or `recon`) and routes (always `produced_by: planner`). Spec: `docs/INCIDENT_OVERLAY_SPEC.md`.
- **Events:** the WebSocket envelope `Event` (type, incident id, trace id, payload).
- **Progress:** the orchestrator's `write_todos` plan as `TodoItem` list plus status.

### 6.2 Call ingestion and extraction (`services/agents/extract.py`, `services/api/transcribe.py`)

- **Sources:** scripted calls (`data/fixtures/calls/*.json`, replayed by the API), typed transcript lines, and **call-in** audio: one device records the caller's monologue in the browser (up to 5 minutes), the API sends it to Gemini in one structured-output pass, and each sentence becomes a caller segment. Every open web app follows a call through `/ws/calls`; a call within `CALL_INCIDENT_WINDOW_S` (default 600 s) of the last joins its incident as the next caller.
- **Extractor:** `EXTRACTOR=gemini` (model) or `keyword` (regex stub for tests and offline use; recognises only a few hazard words). `GeminiExtractor` uses structured output, **verifies every quote against the utterance**, never raises, and falls back to the keyword extractor for a line when the model is slower than `EXTRACT_TIMEOUT_S` (default 4 s) or fails. A code street-address spotter backs the model up so spoken addresses are not missed.
- **Keys the system understands:** `location.address`, `location.name`, `incident.floor`, `incident.visible`, `people.reported`, `people.trapped`, `hazards.reported`, `alarm.sounding`, `access.notes`.
- Only **caller** lines produce facts; call-taker lines never do.

### 6.3 Fusion (`services/agents/fusion.py`)

Pure code, no model. For each extracted fact: a new key creates a `reported` fact at base confidence 0.5 (capped at 0.6 for caller reports); the same value again adds evidence and a small confidence bump; the **same key with a different value from a different call** creates a `Contradiction` and marks the fact `contradicted`. Nothing is auto-resolved: the dispatcher chooses via `POST .../contradictions/resolve`. Fusion also computes open questions, from which the **copilot** (`copilot.py`) derives suggested questions (for example "Is anyone trapped? Which floor?"), including orchestrator-raised questions for life-safety contradictions. List-valued keys (`hazards.reported`, `access.notes`, `incident.visible`) accumulate instead of conflicting: smoke then flames is escalation.

### 6.4 Location (`services/agents/locator.py`)

Builds candidate buildings from the location facts with the CoStar adapter (no model call): each candidate has a score and the reasons behind it (exact or partial address match, name match), coordinates and whether a scan is available. **Auto-confirm** (`AUTO_CONFIRM_LOCATION`, default on) confirms by itself when there is one candidate, or the best scores at least 0.8 and leads the second by at least 0.2; otherwise the dispatcher confirms. A brief is produced only after confirmation. Details: `docs/AUTO_CONFIRM.md`.

### 6.5 Data layers and adapters (`services/agents/providers.py`, `dossier.py`, `tools/`)

- **CoStar:** the hackathon kit export (3,339 Houston and Los Angeles properties) normalized by `scripts/build_costar_index.py` into `data/costar/properties.jsonl` (gitignored: licensed). Fields used: name, use, stories, year built, area, sprinklered where present, tenants. Set `COSTAR_INDEX` to its path.
- **Matterport:** floors, rooms (with boundaries, holes, classifications, metric dimensions) and openings come **live from the Matterport API** through the Node house-data service (`apps/web/server/matterport-plan.mjs`, read-only queries only). From that plan the service derives a *plan index* (rooms, floors, adjacency, size ranks, stairs) used to place callers' words. For catalog houses with no stored data, plans are fetched on demand and cached (`docs/HOUSE_CATALOG.md`). Room types come from a classifier and can be wrong.
- **Vexcel:** oblique views (north, east, south, west: the direction the camera faces) from Vexcel API 2.0, key in the `X-API-KEY` header only. `services/agents/aerial.py` has a vision model read each image **once** (exposures, roof, entries, storeys, with boxes); distances and sides are then computed in code (`scripts/fetch_vexcel.py`).
- **Adapters and fixtures:** every layer sits behind a provider Protocol with a fixture implementation (`data/fixtures/<building>/{costar,matterport,vexcel}.json`). `DATA_MODE=fixture|live` selects; stored real responses for hero buildings in `data/raw/<id>/` (gitignored, sponsor data) are used when present.
- **Dossier** (`dossier.py`): pulls the three layers and merges them into a `BuildingDossier`. **Prefetch** (`scripts/prefetch.py`, cache `cache.py`) stores a building's layers for 24 hours so a brief starts warm.

### 6.6 Rules engine (`config/rules.yaml`, `rules_dossier.py`, `rules_brief.py`)

Pure Python, no model, tested. Every threshold is a configurable placeholder, not an operational standard.

| Rule | What it does |
|---|---|
| R-01 | Height estimate: stories × floor height (3.5 m default), labelled an estimate |
| R-02 | Apparatus reach: `unknown` until a ladder length is configured |
| R-03 | Exposure bands from distance to neighbours (close under 10 m, moderate under 30 m, else low) |
| R-04 | Occupancy hazard map: CoStar use and tenants matched to flags (cooking, flammable liquids, lithium-ion, chemicals, high-piled storage, solvents), status `inferred`, confidence 0.6 |
| R-05 | Egress inventory: counts markers per floor; flags fewer than two stairwells identified |
| R-06 | Data staleness: scan fresh under 365 days, aging under 730; CoStar fresh under 180, aging under 365 |
| R-07 | Occupant-load estimate by use (labelled estimate) |
| R-08 | Contradiction detection (in fusion) |
| R-09 | Confidence aggregation: caller reports capped at 0.6, CoStar record fields 0.9, considerations capped at 0.7 when there is no scan |

### 6.7 Brief assembly and checks (`services/agents/brief.py`, `critic.py`)

A deterministic assembly turns the dossier, incident record and rule outputs into a `Brief`. Consideration checks require: advisory wording (no order words), evidence ids that resolve, **at least one cross-layer consideration** (evidence from more than one layer), references that exist in the overlay, and that crew-marked-wrong markers are left out. `changes_since_last` is a deterministic diff against the previous version (for example "Floor contradicted: 3 vs 4 (Call 2)"). Crew memory (6.11) is applied here as "Crew reported: ..." facts.

### 6.8 The orchestrator (`services/agents/orchestrator.py`, LangChain deepagents)

One persistent deep agent per incident (checkpointer, thread id = incident id). Lifecycle: `start_incident` (pauses at the `confirm_building` interrupt) → `resume_incident` (the dispatcher's choice; writes brief v1) → `update_incident` (brief v2, v3, ... on new facts) → `review_brief` (answers the `publish_brief` interrupt when a consideration is below 0.5 confidence). Guide: `docs/agents-api.md`.

- **Planning:** `write_todos`; the plan changes with the situation (no interior scan, life-safety contradiction) and is shown to the dispatcher as a progress strip.
- **Subagents:** `costar`, `matterport`, `vexcel` (delegated in one turn, run in parallel; each writes a layer file and a short summary), `incident_mapper` (maps reports onto the plan through `resolve_place`), `strategy` (drafts considerations; loads skills on demand), `critic` (checks evidence and wording; can run on a different model provider).
- **Tools** (artifacts are written only by tools): `confirm_building`, `write_copilot_question`, `pull_*` layer tools, `get_transcript`, `get_plan_index`, `resolve_place`, `submit_overlay`, `submit_considerations`, `submit_critique`, `publish_brief`. The model may write only `notes/*.md`.
- **Skills** (`skills/occupancy-hazards`, `skills/size-up-format`): loaded on demand, mounted read-only at `/skills/`.
- **Filesystem:** the incident workspace (below) is mounted at `/workspace/` and is both the agent's filesystem and what the UI renders. Long-term building memory is mounted read-only at `/memories/`.
- **Resilience** (`stalls.py`, `llm.py`): a 12 s client timeout on every model call with one retry that names the stalled agent; `FailoverMiddleware` retries a failed call once on the backup provider; a **run deadline** (`RUN_DEADLINE_S`, default 90 s for v1 and 75 s after) cancels the run and publishes the best partial brief (`Brief.partial`, `partial_reason`). A subagent that fails gives up alone; the run goes on.
- **Tracing:** LangSmith, project name from `LANGSMITH_PROJECT`.

### 6.9 Fast pipeline (`services/agents/fast_path.py`, `docs/FAST_PIPELINE.md`)

`FAST_PIPELINE` (default on): after confirmation, **one structured model call** (no tools, thinking off) reads the transcript with span ids, the fused facts, the plan index, the listing facts, the scan and aerial digests and crew memory, and returns the reports (occupants, fire, hazards, contradictions, objectives) with each place **in the caller's words** (type, floor, over/under/next-to, size), plus two to four considerations. **Code then places every report** with `resolve_place.py` (one matching room → that room; several → floor level plus a dispatcher question; none → floor or unknown): the model never gives a room id. Then overlay assembly and checks (`validate_overlay.py` plus ours) and the brief checks. A failed check triggers one retry with the errors; failing again means nothing is emitted and the agent path takes the incident. At most two runs per call, with a one-second debounce on changes.

### 6.10 Incident overlay and route planner

- **Overlay** (`services/agents/overlay.py`, `packages/schemas/overlay/`): what is placed where, with evidence. Places resolve only through `resolve_place.py`; ambiguous places go to floor level and become a dispatcher question. One `overlay_vN.json` per brief version, emitted as `overlay.updated` right after `brief.updated`.
- **Planner** (`apps/web/public/house-viewer/incident/planner.js`): a deterministic router in the browser. Graph: rooms and doorways from the evacuation graph, floors linked **only** by the plan's stairs, entries at exterior doors and ground-floor windows. Cost is length plus a penalty near reported fire or smoke (it never blocks); a route is flagged `passes_near_fire` and labelled an estimate. `rescue` objectives route entry to target; `recon` objectives route entry to a room or floor. No path means "No route computable". The agent only supplies objectives.
- **Viewer rendering:** person figures with a beacon, fire gradient and heat column, smoke haze, hazard icons, hatched blocked passages, animated numbered routes, "stale" fade after 10 minutes. Everything is paired with a text label.

### 6.11 Persistence

- **Workspace** (`workspace.py`): `StoreWorkspace` on **MongoDB Atlas** when `MONGODB_URI` is set (`mongo_store.py` is a LangGraph store that deepagents' `StoreBackend` mounts at `/workspace/`; one document per file with the parsed JSON queryable), else `FileWorkspace` under `workspace/`. Per incident: `incident.json`, `candidates.json`, `copilot_questions.json`, `layers/*.json`, `dossier.json`, `brief_vN.json`, `critique_vN.json`, `overlay_vN.json`, `todos.json`, `notes/*.md`.
- **Tiger Data** (`db.py`, `scripts/init_tiger.py`): Postgres with TimescaleDB. Tables `incidents`, `facts`, `call_segments` (hypertable), `briefs`, `pipeline_events` (hypertable); a continuous aggregate `stage_seconds_hourly`; the `time_to_brief` view; and the LangGraph `PostgresSaver` so agent threads and incidents survive a restart. Writes are best effort: every function is a no-op when `TIGER_DATABASE_URL` is unset.
- **Memory** (`memory.py`): crew feedback (`wrong`, `outdated`, `confirmed`) on a fact key or marker id is stored per building (MongoDB namespace `memories`, or `data/memories/`) and applied to the next brief and to later incidents at that building.
- **Cache** (`cache.py`): the 24-hour prefetch cache (MongoDB or `data/cache/`).
- **Metrics and audit** (`services/api/metrics.py`, `service.py`): stage timings and an audit log (who viewed a brief, who gave feedback).

### 6.12 LLM layer (`services/agents/llm.py`)

Every chat model comes from here. `LLM_PROVIDER=gemini|openai|xai` selects the main provider (each needs `<PROVIDER>_API_KEY`, `<PROVIDER>_FAST_MODEL`, `<PROVIDER>_VISION_MODEL`). `LLM_FALLBACK_PROVIDER` is the failover for agents, structured calls and extraction; `CRITIC_PROVIDER` runs the orchestrator's critic on a different model than the drafter (recorded as `critic_model` in the critique). Call-in transcription always uses Gemini because the other providers take no audio.

## 7. API reference

FastAPI app, docs at `/docs`. Roles come from the `X-Role` header (default `dispatcher`); only `/api/admin/data-mode` requires `admin`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | data mode, pipeline, extractor, LLM providers, Tiger status, recent live fallbacks |
| POST | `/api/incidents` · GET `/api/incidents` · GET/DELETE `/api/incidents/{id}` | create, list, read, delete |
| POST | `/api/incidents/{id}/segments` | one `CallSegment`; returns fusion changes and the extracted facts |
| GET | `/api/calls/scripts` · POST `/api/calls/simulate` | list and replay scripted calls (`realtime`, `speed`, optional confirm) |
| POST | `/api/calls/web/callin` · `/callin/audio` · `/end` · `/clear` | the call-in recorder: start, upload audio, end, clear |
| GET | `/api/incidents/{id}/candidates` | ranked location candidates |
| POST | `/api/incidents/{id}/confirm-location` | `{building_id}`; returns 202, the brief arrives as an event |
| POST | `/api/incidents/{id}/review` | publish a brief held for low confidence, optionally dropping considerations |
| POST | `/api/incidents/{id}/contradictions/resolve` | `{key, value}` |
| GET | `/api/buildings` · `/api/buildings/{id}/dossier` | buildings and their dossier |
| GET | `/api/incidents/{id}/briefs` · `/briefs/latest` · `/briefs/{version}` | brief versions (views are audited) |
| GET | `/api/incidents/{id}/overlay/latest` | the latest overlay |
| GET | `/api/incidents/{id}/copilot` · POST `/api/copilot/suggestion/{id}/dismiss` · `/asked` | copilot questions |
| POST/GET | `/api/incidents/{id}/feedback` | crew feedback `{fact_key, verdict, note?}` |
| GET | `/api/incidents/{id}/audit` | audit log |
| GET | `/api/metrics/time-to-brief` · `/api/metrics/stages` | timings: time-to-brief reads Tiger's `time_to_brief` view (`source: tiger`; `n`, `n_fallback`, `p50_ms`, `p95_ms`, `runs`), falling back to local files when Tiger is off |
| WS | `/ws/incidents/{id}` · `/ws/calls` | incident events (with a snapshot on connect) and call-in events |

**WebSocket events** (`/ws/incidents/{id}`): `transcript.segment` (with `changes` and `extracted`), `incident.updated`, `contradiction.raised`, `location.candidates`, `dossier.progress` (the plan), `brief.updated` (`version`, `status`, `published`, `needs_review`, `fallback_reason`, the brief), `overlay.updated` (`version`, `overlay`), `copilot.suggestion`, `metric.tick`. Every event carries `incident_id`, a timestamp and a trace id.

## 8. The web app

`apps/web`: React 19, Vite, Tailwind, and a Three.js house viewer served as static files.

- **Views** (`App.tsx`): Dispatcher and MDT. The top bar decides **LIVE** vs **MOCK DATA** by calling `/api/calls/scripts` (not `/health`, which Vite does not proxy).
- **Data layer:** `src/api/client.ts` (REST), `src/api/useLiveIncident.ts` (state plus one WebSocket per incident with reconnect; hydrates candidates, the latest brief and copilot questions from REST after a reconnect), `src/live/` (call-in recording and the call feed), `src/house/` (house screen, incident socket, house data).
- **Components:** `DispatcherConsole`, `MdtView`, `Inspector` (evidence for a selected fact, marker or consideration, plus the crew feedback buttons), `MetricsChip` (time to brief in the top bar), `TranscriptComposer`, `CallIn`, `ScriptPicker`, `ConfirmGate`, `PlanList`, `DemoControls`, and `viewer/BuildingViewer` (the iframe wrapper).
- **House viewer** (`public/house-viewer/`): `casa.html`/`casa.js` (3D scene and 2D SVG plan), `evac.js` (evacuation graph), `escaleras.js` (stair pairing), `incident/` (overlay validation, plan index, planner, scenarios). It talks to its host with `postMessage`. URL parameters and the postMessage API are in `public/house-viewer/README.md`.
- **house-data service** (`apps/web/server/`): serves `/api/house-viewer/*` from the Vite dev server (`house-data.mjs`) or standalone in production (`standalone.mjs`): live Matterport plans, plan index, quality, stairs and Vexcel oblique views, cached in memory for six hours. Credentials stay on the server.
- **URL parameters:** `?mock=1` (force mock data), `?admin=1` (demo controls), `?incident=<id>` (attach to an incident), `?sample=<id>` (house), `?viewer=0` (old map and tabs), `?debug=1` (viewer scenario buttons). iPad layout notes: `docs/IPAD.md`.

## 9. Repository layout

```
FlameScout/
├── README.md                     this file
├── CLAUDE.md                     working notes and rules for the coding agents
├── packages/schemas/             Pydantic models, exported JSON Schema, overlay spec files and validators
├── services/
│   ├── agents/                   extract, fusion, locator, dossier, brief, rules_*, critic, copilot, overlay,
│   │                             fast_path, orchestrator, llm, stalls, memory, cache, workspace, mongo_store, db,
│   │                             aerial, house_catalog, providers; tools/ (costar_kit, geocode, house_data_client, vision, rules)
│   └── api/                      main (routes), service (IncidentService), events (bus), metrics, calls,
│                                 transcribe, config, store, tiger_schema.sql
├── apps/web/
│   ├── src/                      React app (api, components, house, live, mock, types)
│   ├── public/house-viewer/      Three.js viewer, evacuation graph, incident layer and route planner
│   └── server/                   Node house-data service (Matterport plans, Vexcel imagery, catalog)
├── skills/                       deepagents skills: occupancy-hazards, size-up-format
├── config/rules.yaml             rules engine thresholds and hazard map (placeholders, not standards)
├── data/
│   ├── fixtures/                 per-building layer fixtures and scripted calls (calls/)
│   ├── costar/                   normalized CoStar index (gitignored, licensed)
│   └── raw/                      stored sponsor API responses (gitignored, never commit)
├── scripts/                      run_incident, run_fusion, prefetch, build_costar_index, fetch_vexcel,
│                                 make_plan_index, init_tiger, export_schemas, check_failover, build_house_catalog, mock_incident_ws
├── deploy/                       Caddyfile, deploy.sh, vultr-bootstrap.sh
├── docker-compose.yml, Dockerfile
├── tests/                        pytest suite (about 30 modules)
└── docs/                         PRD, API and agent guides, overlay spec, deployment, demo and pitch notes
```

## 10. Setup and running

Requires **Python 3.11+** and **Node 22+**. Keys go in `.env` at the repo root (copy `.env.example`; it is gitignored). The API and the Vite server read `.env` only at startup: restart after editing it.

```bash
# Backend (from the repo root)
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                  # installs dependencies; code is imported from the repo root
uvicorn services.api.main:app --port 8000                # docs at http://localhost:8000/docs
                                                         # run from the repo root, one worker, no --reload for demos

# Frontend
cd apps/web && npm ci && npm run dev                     # http://localhost:5173 (proxies /api and /ws to :8000)
```

**Try it.** Open the app, pick a scripted call in the top bar (for example **S2**: two callers and a floor contradiction; or a `SHOW_*` call on a scanned house) and press **Run call**. The Dispatcher view fills in; confirm the location (or let it auto-confirm); the MDT shows the plan, then the brief, then a new version when the second call arrives. Choose **Blank** to type your own caller lines. Scripts are described in `docs/CALL_FIXTURES.md` and `docs/LOCAL_DEMO.md`.

**Without any API keys:** `EXTRACTOR=keyword`, `DATA_MODE=fixture`, and the mock data (`?mock=1`) run offline; tests need no network.

**Command-line tools**

| Command | What it does |
|---|---|
| `python scripts/run_fusion.py S2` | replay a call script through fusion |
| `python scripts/run_incident.py S2` | one incident end to end: call 1, confirm, brief v1, call 2, brief v2 (`--cold` ignores prefetch; `--drop-low` answers a held brief by dropping) |
| `python scripts/prefetch.py <building_id>` | prefetch a building's layers for 24 hours |
| `python scripts/build_costar_index.py <kit folder>` | build `data/costar/properties.jsonl` from the CoStar kit |
| `python scripts/fetch_vexcel.py <id>` | download a hero building's Vexcel images and read them |
| `python scripts/init_tiger.py` | create the Tiger Data tables, hypertables and aggregate (once) |
| `python scripts/export_schemas.py` | export JSON Schema for the frontend |
| `python scripts/check_failover.py` | force a timeout on the primary model and check failover |

## 11. Configuration reference

| Variable | Purpose |
|---|---|
| `DATA_MODE` | `fixture` (default; the demo never depends on live APIs) or `live` |
| `PIPELINE` | `orchestrator` (default) or `rules` (plain rules pipeline only) |
| `FAST_PIPELINE` | `1` (default): one structured call writes overlay and considerations; the agent path is its fallback |
| `AUTO_CONFIRM_LOCATION` | `1` (default): confirm the location when the locator is sure |
| `EXTRACTOR`, `EXTRACT_TIMEOUT_S` | `gemini` or `keyword`; per-line timeout (default 4 s) |
| `GEMINI_API_KEY`, `GEMINI_FAST_MODEL`, `GEMINI_VISION_MODEL`, `GEMINI_OVERLAY_MODEL` | Gemini; model IDs come from AI Studio, never hard-coded |
| `XAI_*`, `OPENAI_*` (key, fast and vision model; `OPENAI_BASE_URL`, `OPENAI_REASONING_EFFORT` optional) | alternative providers |
| `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`, `CRITIC_PROVIDER` | main provider, failover provider, critic provider |
| `RUN_DEADLINE_S` | wall-clock cap per orchestrator run before a partial brief is published |
| `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT` | tracing |
| `COSTAR_INDEX` | path to the normalized CoStar index |
| `MATTERPORT_API_KEY` + `MATTERPORT_API_SECRET` (or `MATTERPORT_API_KEY=<id>:<secret>`), `VEXCEL_API_KEY` | live plans and aerial imagery (server side only) |
| `HOUSE_DATA_URL` | the house-data service the backend asks for plans (default `http://127.0.0.1:8080`) |
| `TIGER_DATABASE_URL` | Postgres on Tiger Data; unset means local files |
| `MONGODB_URI`, `MONGODB_DB` | MongoDB Atlas workspace; unset means local files |
| `CALL_INCIDENT_WINDOW_S` | a second call within this window joins the same incident (default 600) |
| `CORS_ORIGINS`, `SITE_ADDRESS`, `GEOCODER`, `MAP_TOKEN` | CORS, Caddy's HTTPS name, geocoder, map token |

## 12. Deployment (Vultr)

One Vultr Cloud Compute instance (Ubuntu 24.04, 2 vCPU and 4 GB RAM or more) runs three containers:

- **api**: FastAPI and the agents (port 8000, Docker network only).
- **house-data**: the Node service for `/api/house-viewer/*` (port 8080, Docker network only).
- **web**: Caddy, which serves the built UI and forwards `/api/house-viewer/*` to house-data and `/api`, `/ws`, `/health`, `/docs` to api, from one address (no CORS; WebSockets use `wss://`). Caddy gets certificates automatically when `SITE_ADDRESS` is a domain (or `<ip-with-dashes>.sslip.io`); `:80` gives plain HTTP.

The databases stay managed (Tiger Data and MongoDB Atlas). Secrets and the licensed CoStar index are never built into an image; they are copied to the server and read at runtime. A bare IP cannot get a certificate: use a domain.

```bash
export VULTR_IP=<ip>
ssh root@$VULTR_IP "REPO_URL=$(git remote get-url origin) bash -s" < deploy/vultr-bootstrap.sh   # once: Docker, firewall, clone
VULTR_HOST=root@$VULTR_IP deploy/deploy.sh --sync    # first deploy: copies .env and the CoStar index
VULTR_HOST=root@$VULTR_IP deploy/deploy.sh           # later: git pull, rebuild, restart
```

`--sync` overwrites the server's `.env` with yours, so use it deliberately. Full guide: `docs/deploy-vultr.md`.

## 13. Testing

```bash
pytest -q                          # offline suite (several hundred tests; live and data-dependent tests skip)
RUN_LIVE=1 pytest -q -m live       # live model and database tests (uses API quota)
node apps/web/server/tests/validate.test.mjs     # overlay validation
node apps/web/server/tests/plan-index.test.mjs   # plan index (LIVE=1 uses the live API)
```

Deterministic modules (rules, fusion, brief checks, overlay, place resolution) have tests; live tests check extraction and a full orchestrator run against the real model. One orchestrator test, `test_run_deadline_publishes_a_facts_only_partial_brief` (the `[files]` variant), failed in our latest run on this tree: it expects the run deadline to trip "during resume" but it tripped "during start". It is timing-dependent; check it before relying on the deadline behaviour.

## 14. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The app says **MOCK DATA** | The API is not answering. Start it from the repo root. The check calls `/api/calls/scripts` through the dev proxy; `/health` is not proxied. Remove `?mock=1` if present. |
| `npm run dev` says "Port 5173 is in use" | An older dev server holds the port; use the new port it prints, or stop the old one. |
| Changes to `.env` have no effect | The API and Vite read `.env` only at startup. Restart both. |
| Viewer shows "VIEWER UNAVAILABLE" or "Matterport API token is not set" | Add the Matterport key and secret to the repo-root `.env` and restart `npm run dev`. |
| Vexcel returns 401 | Vexcel rejected the key. Test it directly with `curl -H "X-API-KEY: <key>" https://api.vexcelgroup.com/v2/ortho/collections?wkt=POINT(-118.2437 34.0522)&srid=4326`; a 401 there means a new key is needed. |
| `429 RESOURCE_EXHAUSTED` from Gemini | Free-tier rate limit (5 requests per minute per model). Enable billing, use a higher-quota model, or set `LLM_FALLBACK_PROVIDER`. |
| Hazards are missing from facts | `EXTRACTOR=keyword` only knows a handful of hazard words. Set `EXTRACTOR=gemini`. |
| `UnicodeDecodeError` building the CoStar index on Windows | The kit contains UTF-8 characters. `costar_kit.py` and the index reader now open files as UTF-8; on an older checkout, run Python with `-X utf8` (or `PYTHONUTF8=1`). |
| The deployed site does not open on one network | Some networks filter newer TLDs such as `.health`. Check from the server or a phone hotspot. |
| `python -m venv .venv` fails | A **file** named `.venv` (a renamed `.env`) is in the way. Rename it back to `.env`. |

## 15. Measured performance

Measured during development (a few dozen runs, not a benchmark). Re-measure before quoting.

| Measure | Result |
|---|---|
| Caller line to updated record (fusion) | median about 0.36 s over 97 lines (local); target 3 s |
| Confirmed address to first brief, fast path | about 8 s median on the Vultr deployment (4 runs); 3.9 s on one local run |
| Confirmed address to first brief, agent path | median about 63 s, p95 about 122 s over 11 briefs (Tiger Data view) |
| Failed agent runs | 13 recorded runs ended in a facts-only fallback brief; the cause was not recorded |

`GET /api/metrics/stages` and `/api/metrics/time-to-brief` report these. The time-to-brief percentiles come from the Tiger `time_to_brief` view and exclude facts-only fallbacks (reported separately as `n_fallback`).

## 16. Sponsor technologies

| Technology | Where it is used |
|---|---|
| **CoStar** | Property search and records for the locator and dossier (3,339-property kit index); tenant types drive hazard flags. |
| **Matterport** | Live floor plans, rooms, doors and stairs; the evacuation graph, 3D and 2D plan, and routes. |
| **Vexcel** | Oblique aerial views; a vision model labels exposures, roof and entries, geometry computed in code. |
| **LangChain deepagents + LangSmith** | The incident orchestrator: planning, parallel subagents, filesystem, human-in-the-loop interrupts, skills, memory; traced in LangSmith. |
| **Gemini** | Fact extraction, the agents, aerial image reading, call-in transcription, the fast pipeline. xAI and OpenAI are supported alternatives; xAI is the failover and critic. |
| **Tiger Data** | Time-series tables, a continuous aggregate, the `time_to_brief` view and agent checkpoints. |
| **MongoDB Atlas** | The agents' shared incident workspace, the prefetch cache and building memory. |
| **Vultr** | Docker Compose deployment behind Caddy with HTTPS. |
| **ChatOverflow** | Our coding agents shared verified findings from the build. |

## 17. Client consultation (Peri Forward Deployed Challenge)

**Clients:** Dartmouth Safety and Security and the Hanover Fire Department.

**Dartmouth Safety and Security.** We consulted a officer from DOSS by phone on Nov 14, 2026 at 13:04. The call was not recorded, because the office's policy doesn't allow a formal interview; he gave us personal permission to paraphrase. What he told us (paraphrased from our notes):

- Safety and Security responds to every campus fire alarm and supports evacuation, building access, and escorting responders.
- Command passes to Hanover Fire on arrival, and Safety and Security tells Hanover Fire the location and nature of the call.
- Hanover Fire assesses the situation on scene before acting.
- He took part in the Safety and Security response to the Morton Hall fire in 2016.

**Hanover Fire Department.** What we know about Hanover Fire's role comes secondhand from Safety and Security, since they were busy.

**Public record we checked:** the Morton Hall four-alarm fire of Oct 1, 2016 (Valley News, WMUR, The Dartmouth, Dartmouth news). Crews came from about ten Upper Valley departments. WMUR reported that the building's design challenged firefighters and that visibility on the fourth floor was near zero on arrival. The fire lasted about 6 hours.

**Our premise.** Size-up is essential because it aligns the crew and sets priorities, but every minute spent gathering information at the curb is a minute not spent on rescue and suppression. We aim to have most of that information ready before arrival, so the on-scene size-up confirms and refines it instead of starting from nothing.

**How it shaped our design**

- **We follow the handoff, not replace it.** The Officer described the handoff to Hanover Fire as the location and nature of the call, followed by an on-scene assessment before action. FlameScout replaces neither. The MDT's top bar opens with the same two items, the confirmed address and the incident type, and the building detail sits underneath.
- **Advisory, because command sits with Hanover Fire.** Considerations use "Consider ..." wording, never orders.
- **Every brief shows how old its data is.** Morton Hall showed how much a building can change over the years, so the top bar shows the age of the interior scan and the date of the CoStar record, and the size-up lists when the building was built where the record has it. A building with no scan says so and lists its gaps.
- **A building that fights back is why interior detail matters.** WMUR's report that the design challenged firefighters and that fourth-floor visibility was near zero is the case for knowing stairs, entries and floors before arrival, which is what the scan-based plan, routes and floor-level reports provide.

## 18. Known limits and roadmap

**Known limits**

- Routes and distances are estimates from partial scans and caller reports. Room types come from a classifier and can be wrong. Verify on site.
- Interior data exists only for buildings with a Matterport scan; others show gaps. Some bundled scenarios (for example the Wilshire Tower example) use synthetic fixture data.
- CoStar data comes from the hackathon kit export, not a live CoStar API. CoStar, Matterport and Vexcel responses are licensed and are not in the repository.
- The full agent path takes a minute or more and some runs fall back to facts-only briefs; the fast path is quicker but depends on its checks passing.
- Crew feedback applies to facts, hazards and markers only (not considerations), and takes effect on the next brief version.
- Fusion and the model only read **caller** lines; call-taker lines never produce facts.
- The metrics module also contains writes to tables from an older schema; they fail quietly and the real data is written by `db.py`.

**Roadmap:** live CAD/MDT integration, hydrant and water-supply data, a structured crew-review loop and feedback UI, multilingual transcripts, an offline cache of district buildings, phone or drone scan updates, and EMS and other modules.

## 19. Documentation index

| Doc | What it covers |
|---|---|
| [docs/PRD.md](docs/PRD.md) | the product requirements and original plan |
| [docs/backend.md](docs/backend.md) | the API and the rules pipeline |
| [docs/agents-api.md](docs/agents-api.md) | driving the orchestrator, storage, sessions, timings |
| [docs/FAST_PIPELINE.md](docs/FAST_PIPELINE.md) | the one-call overlay and brief path |
| [docs/AUTO_CONFIRM.md](docs/AUTO_CONFIRM.md) | location auto-confirmation and event shapes |
| [docs/INCIDENT_OVERLAY_SPEC.md](docs/INCIDENT_OVERLAY_SPEC.md) | overlay contract, routes and what the viewer draws |
| [docs/OVERLAY_E2E.md](docs/OVERLAY_E2E.md) | end-to-end overlay checks |
| [docs/CALL_FIXTURES.md](docs/CALL_FIXTURES.md) | the synthetic call scripts and expected facts |
| [docs/HOUSE_CATALOG.md](docs/HOUSE_CATALOG.md) | the house catalog and on-demand plans |
| [docs/LOCAL_DEMO.md](docs/LOCAL_DEMO.md) | the viewer scenarios and demo steps |
| [docs/IPAD.md](docs/IPAD.md) | the iPad layout |
| [docs/deploy-vultr.md](docs/deploy-vultr.md) | deploying on Vultr |
| [docs/PITCH_BRIEFS.md](docs/PITCH_BRIEFS.md) | per-track judging notes: criteria, talking points, likely questions |
| [docs/DEVPOST.md](docs/DEVPOST.md) | the Devpost submission text and track checklist |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | the timed 2-minute demo script and fallbacks |
| [apps/web/public/house-viewer/README.md](apps/web/public/house-viewer/README.md) | the viewer's URL parameters, data routes and postMessage API |
