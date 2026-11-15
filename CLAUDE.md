# FlameScout

FlameScout turns live fire-dispatch calls into a building-specific size-up on the crew's MDT
before the engine arrives. HackPrinceton Fall 2026 (main track: Healthcare). Submission deadline:
8:00 AM Sunday Nov 15 (hard). Full spec: docs/PRD.md. When the PRD and this file disagree, this file wins.

## Commands
- Install: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Tests: `pytest -q` (offline). Live API tests: `RUN_LIVE=1 pytest -q -m live`
- Replay a call script through fusion: `python scripts/run_fusion.py S2`
- Run an incident end to end (call 1 -> dispatcher confirms -> brief v1, call 2 -> brief v2): `python scripts/run_incident.py S2`
  (`--cold` ignores prefetched building data). `S4B` is hero 23296 (9700 Tanner Rd, no interior scan, needs
  `data/raw/23296`); `S4Q` is the synthetic no-scan building: re-plan, and a
  low-confidence review that `--drop-low` answers by dropping instead of approving.
  `S5` is Bruno's overlay story on `fake_cedar_house` (synthetic v2 plan index in its fixtures);
  `S6` is 9660 Fairdale Lane (hero 209217448, real scan; needs a v2 `data/raw/209217448/derived/plan_index.json`).
- Prefetch a building's data layers for the next 24 h: `python scripts/prefetch.py fake_wilshire_tower`
- Export JSON Schema for the frontend: `python scripts/export_schemas.py`
- API: `uvicorn services.api.main:app --reload --port 8000` (docs at /docs, WS at /ws/incidents/{id})
- Tiger Data schema (once): `python scripts/init_tiger.py`
- Real CoStar data (once): `python scripts/build_costar_index.py ~/Downloads/costar-hackathon-kit`
  -> `data/costar/properties.jsonl` (gitignored, licensed); set `COSTAR_INDEX` in `.env`.

## Layout
- `packages/schemas/flamescout_schemas/` - Pydantic models. The contract between agents, API and UI.
  Change a model -> run export_schemas.py and tell the frontend person.
- `services/agents/extract.py` - per-utterance fact extraction (KeywordExtractor stub, GeminiExtractor;
  `EXTRACTOR=keyword|gemini`, default keyword; `EXTRACT_TIMEOUT_S`, default 4.0).
- `services/agents/fusion.py` - deterministic merge, contradictions, open questions. No LLM.
- `services/agents/orchestrator.py` - one persistent deep agent per incident (checkpointer, thread =
  incident id); subagents costar, matterport, vexcel (delegated in one turn, run in parallel),
  strategy, critic. `start_incident` pauses at the `confirm_building`
  interrupt, `resume_incident` takes the dispatcher's choice, `update_incident` writes the next brief
  version, `review_brief` answers the `publish_brief` interrupt (a consideration below 0.5 confidence).
  The plan in write_todos changes with the situation (no interior scan, life-safety contradiction).
  JSON artifacts are written only by its tools; the model may write only `notes/*.md`.
  API guide: `docs/agents-api.md`.
- `services/agents/providers.py` - PRD 7.4 adapter Protocols. `Live*` adapters read the heroes' stored real API
  responses in `data/raw/<id>/` (gitignored, never commit) whenever that folder exists, else the fixtures.
  No network calls (`DATA_MODE=live` logs "not configured"). Read `data/raw/*/README.md` and `FIELD_NOTES.md` first.
- `services/agents/aerial.py` - Vexcel images: one Gemini vision reading per image (`read_building`, run by
  `scripts/fetch_vexcel.py <id>`, which also downloads them; needs VEXCEL_API_KEY), stored in
  `data/raw/<id>/derived/aerial_reading.json`; then, with no LLM, the geometry: distances, sides, lat/lon, storeys.
- `services/agents/locator.py` - candidate buildings for an incident via the CoStar adapter. No LLM.
- `services/agents/brief.py` - deterministic Brief assembly, consideration checks (incl. at least one
  cross-layer consideration), `changes_since_last` diff. No LLM.
- `services/agents/memory.py` - long-term building memory: crew feedback (`feedback(incident_id, item_id,
  verdict, note)`) kept per building (MongoDB namespace `["memories"]`, or `data/memories/`, not committed),
  mounted read-only at `/memories/`; `brief.py` applies it ("Crew reported: ..." facts, wrong markers left out).
- `services/agents/overlay.py` - incident overlay for the 3D viewer (`docs/INCIDENT_OVERLAY_SPEC.md`): transcript
  spans, plan index (`data/raw/<id>/derived/plan_index.json`, missing = no scan), assembly, checks (Bruno's
  `validate_overlay.py` + ours), version diff. The `incident_mapper` subagent submits; one `overlay_vN.json`
  per brief version. Places resolve only through Bruno's `packages/schemas/overlay/resolve_place.py`
  (mapper tool `resolve_place`); ambiguous -> floor level + a question in `copilot_questions.json`. No LLM in this module.
- `services/agents/stalls.py` - 12 s client timeout on every orchestrator / subagent model call, one retry,
  each stall and retry logged with the agent name and counted in `RunResult`. `FailoverMiddleware`: a failed
  call goes once to the backup provider. A subagent that still fails gives up alone (the run goes on).
  `RUN_DEADLINE_S` (default 90 s for v1, 75 s after): past it the run is cancelled and the best partial
  brief is published (`Brief.partial`, `partial_reason`). Nothing may make a run hang.
- `services/agents/llm.py` - every chat model. `LLM_PROVIDER=gemini|openai|xai` (xAI via langchain-xai); `LLM_FALLBACK_PROVIDER` = failover
  for agents, `structured()` and extraction; `CRITIC_PROVIDER` = the orchestrator's critic on a different model
  than the drafter (backed by LLM_PROVIDER), recorded as `critic_model` in critique_vN.json. Both unset = one provider.
- `services/agents/copilot.py` - also holds the orchestrator's dispatcher questions for life-safety
  contradictions (`copilot_questions.json` in the incident workspace; `copilot.json` is the API's status map).
- `skills/<name>/SKILL.md` - deepagents skills the strategy subagent loads on demand (mounted read-only at
  `/skills/`): `occupancy-hazards`, `size-up-format`.
- `services/agents/dossier.py` - pulls the costar / matterport / vexcel layers through the adapters and
  merges them into a BuildingDossier. No LLM.
- `services/agents/cache.py` - prefetch cache (24 h): MongoDB namespace `["cache", <building_id>]`, or
  `data/cache/<building_id>/` (not committed) when MongoDB is off.
- `services/agents/workspace.py` - `Workspace`: where incident documents live. `StoreWorkspace` on MongoDB
  Atlas when `MONGODB_URI` is set (`mongo_store.py`, a LangGraph BaseStore that deepagents' StoreBackend
  mounts at /workspace/), else `FileWorkspace` under `workspace/`. Never read/write those files directly.
- `services/agents/db.py` - Postgres on Tiger Data (`TIGER_DATABASE_URL`): incidents, facts, call_segments
  and pipeline_events (hypertables), briefs, `time_to_brief` view, and the LangGraph `PostgresSaver`
  (incidents survive a restart). Best-effort writes; every function is a no-op when the URL is unset.
- `data/fixtures/calls/` - scripted calls S1-S5, S4B (hero 23296) and S4Q (no-scan building in the orchestrator's layer
  format, `fake_quarry_depot`; S4's `maple_lofts` exists only in the services/api fixture format).
  `data/fixtures/<building_id>/{costar,matterport,vexcel}.json`
  - per-layer fixtures, our own normalized fields only.
- Incident workspace (MongoDB, or `workspace/<incident_id>/`): incident.json, candidates.json, copilot_questions.json, layers/*.json,
  dossier.json, brief_vN.json, critique_vN.json, overlay_vN.json, places_vN.json (resolve_place calls), todos.json = the orchestrator's plan + status for the UI
  progress strip, notes/*.md. This is the deepagent filesystem AND what the UI renders. Not committed.
- `services/agents/{rules_dossier,rules_brief,critic,copilot}.py`, `services/agents/tools/` - the earlier
  plain-function pipeline used by `services/api/` (rules engine R-01..R-09 in `config/rules.yaml`, provider
  adapters with live fallback, geocode, vision). `tools/costar_kit.py` normalizes the CoStar hackathon kit
  (3,339 Houston/LA properties); with `COSTAR_INDEX` set it serves CoStar in every mode.
- `services/api/` - FastAPI app (IncidentService, event bus, metrics). `PIPELINE=orchestrator` (default):
  briefs come from the orchestrator (start on candidates, resume on confirm, update on new facts,
  `POST /api/incidents/{id}/review`); an incident falls back to the rules pipeline above when its building
  is not an orchestrator candidate or an orchestrator call raises. `PIPELINE=rules`: rules only.
  Confirm-location returns 202; briefs arrive as `brief.updated` (`pipeline`, `status`, `published`,
  `needs_review`, `fallback_reason`), each followed by `overlay.updated` (`version`, `overlay`) when the
  producer wrote `overlay_vN.json` (docs/INCIDENT_OVERLAY_SPEC.md). Brief work runs on one serial worker per incident.
  Briefs from both pipelines live in the orchestrator workspace; the API's own files in `workspace/_api/`.
  Feedback goes through `memory.feedback()` only.
- `services/api/transcribe.py` - Call in (header button, `apps/web/src/components/CallIn.tsx`): one device records the
  caller's whole monologue (`src/live/monologueRecorder.ts`, max 5 min); Done posts it to `/api/calls/web/callin/audio`,
  Gemini transcribes it in one go (structured output, sentences) and each sentence becomes a caller segment. Every open
  web app follows the call via `/ws/calls` (`src/live/callFeed.ts`; bus topic `_calls`, active calls sent on connect).
  The incident socket replays the transcript so far to a client that joins late.
  `services/api/calls.py`: the call registry (call -> incident + c1/c2; a call within `CALL_INCIDENT_WINDOW_S`, default
  600, of the last one joins its incident as the next caller).

## Non-negotiable rules
- Advisory only: considerations say "Consider ...", never orders.
- Every Fact/Marker/Consideration has status, confidence and evidence_ids that resolve in the
  `evidence` dict. If unsupported -> status "unverified". Never fill gaps with guesses.
- Deterministic first: distances, heights, counts, contradictions are computed in code, never by an LLM.
- LLMs only: extract facts from one utterance, read images (Gemini vision), draft considerations,
  critique. Always structured output (`with_structured_output(PydanticModel)`), retry on schema error.
- Never invent third-party API fields (CoStar/Matterport/Vexcel/Photon). Build behind the adapter
  Protocols in PRD 7.4, log what's missing, fall back to fixtures. `DATA_MODE=live|fixture`.
- Simulation only: never call real emergency numbers.
- Secrets only in `.env` (gitignored). Never commit keys.

## Agent architecture (deepagents)
- Fusion is NOT inside the deep agent loop (latency target < 3 s). It runs per segment as plain code.
- One orchestrator `create_deep_agent` per incident, triggered when the dispatcher confirms the
  building. Subagents: dossier (CoStar / Matterport / Vexcel pulls in parallel), strategy, critic.
  Locator and copilot are plain functions.
- Use: `write_todos` (shown in UI as a progress strip), subagents, filesystem as the workspace,
  `interrupt_on` for the location-confirmation gate. LangSmith tracing on.
- Before writing deepagents code, check the INSTALLED version's real signature
  (`python -c "import deepagents, inspect; print(inspect.signature(deepagents.create_deep_agent))"`)
  and its README. Do not assume parameter names from memory.
- Gemini via `langchain-google-genai`. Model IDs come from env (`GEMINI_FAST_MODEL`,
  `GEMINI_VISION_MODEL`); confirm current IDs in AI Studio, don't hard-code guesses.

## Conventions
- Python 3.11+, type hints, small functions, tests for every deterministic module.
- Commit small and often. Repo is public.
- Commit directly to `main` (no feature branches): `git pull` first, then commit and push.
- Timebox: if a feature isn't working in its estimate, fall back to the fixture and move on.

## TODO (later)
- Implement the live CoStar / Matterport / Vexcel adapters in `providers.py` once real API access and
  field names are confirmed (today `DATA_MODE=live` serves fixtures).
