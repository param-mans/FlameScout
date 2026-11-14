# FlameScout

FlameScout turns live fire-dispatch calls into a building-specific size-up on the crew's MDT
before the engine arrives. HackPrinceton Fall 2026 (main track: Healthcare). Submission deadline:
8:00 AM Sunday Nov 15 (hard). Full spec: docs/PRD.md. When the PRD and this file disagree, this file wins.

## Commands
- Install: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Tests: `pytest -q` (offline). Live API tests: `RUN_LIVE=1 pytest -q -m live`
- Replay a call script through fusion: `python scripts/run_fusion.py S2`
- Run an incident end to end (call 1 -> dispatcher confirms -> brief v1, call 2 -> brief v2): `python scripts/run_incident.py S2`
  (`--cold` ignores prefetched building data). `S4B` is a building with no interior scan: re-plan, and a
  low-confidence review that `--drop-low` answers by dropping instead of approving.
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
- `services/agents/providers.py` - PRD 7.4 adapter Protocols, fixture implementations, live stubs
  (`DATA_MODE=live` logs "not configured" and serves fixtures).
- `services/agents/locator.py` - candidate buildings for an incident via the CoStar adapter. No LLM.
- `services/agents/brief.py` - deterministic Brief assembly, consideration checks (incl. at least one
  cross-layer consideration), `changes_since_last` diff. No LLM.
- `services/agents/stalls.py` - 12 s client timeout on every orchestrator / subagent model call, one retry,
  each stall and retry logged with the agent name and counted in `RunResult`.
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
- `data/fixtures/calls/` - scripted calls S1-S4, plus S4B (no-scan building in the orchestrator's layer
  format, `fake_quarry_depot`; S4's `maple_lofts` exists only in the services/api fixture format).
  `data/fixtures/<building_id>/{costar,matterport,vexcel}.json`
  - per-layer fixtures, our own normalized fields only.
- Incident workspace (MongoDB, or `workspace/<incident_id>/`): incident.json, candidates.json, copilot_questions.json, layers/*.json,
  dossier.json, brief_vN.json, critique_vN.json, todos.json = the orchestrator's plan + status for the UI
  progress strip, notes/*.md. This is the deepagent filesystem AND what the UI renders. Not committed.
- `services/agents/{rules_dossier,rules_brief,critic,copilot}.py`, `services/agents/tools/` - the earlier
  plain-function pipeline used by `services/api/` (rules engine R-01..R-09 in `config/rules.yaml`, provider
  adapters with live fallback, geocode, vision). `tools/costar_kit.py` normalizes the CoStar hackathon kit
  (3,339 Houston/LA properties); with `COSTAR_INDEX` set it serves CoStar in every mode.
- `services/api/` - FastAPI app (IncidentService, JSON workspace store, event bus, metrics). Still on the
  rules pipeline above, not yet on the orchestrator / MongoDB workspace / `db.py`.

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
