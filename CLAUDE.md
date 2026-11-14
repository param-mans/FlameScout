# FlameScout

FlameScout turns live fire-dispatch calls into a building-specific size-up on the crew's MDT
before the engine arrives. HackPrinceton Fall 2026 (main track: Healthcare). Submission deadline:
8:00 AM Sunday Nov 15 (hard). Full spec: docs/PRD.md. When the PRD and this file disagree, this file wins.

## Commands
- Install: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Tests: `pytest -q` (offline). Live API tests: `RUN_LIVE=1 pytest -q -m live`
- Replay a call script through fusion: `python scripts/run_fusion.py S2`
- Run an incident end to end (call 1 -> dispatcher confirms -> brief v1, call 2 -> brief v2): `python scripts/run_incident.py S2`
  (`--cold` ignores prefetched building data)
- Prefetch a building's data layers for the next 24 h: `python scripts/prefetch.py fake_wilshire_tower`
- Export JSON Schema for the frontend: `python scripts/export_schemas.py`

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
  version. JSON artifacts are written only by its tools; the model may write only `notes/*.md`.
  API guide: `docs/agents-api.md`.
- `services/agents/providers.py` - PRD 7.4 adapter Protocols, fixture implementations, live stubs
  (`DATA_MODE=live` logs "not configured" and serves fixtures).
- `services/agents/locator.py` - candidate buildings for an incident via the CoStar adapter. No LLM.
- `services/agents/brief.py` - deterministic Brief assembly, consideration checks (incl. at least one
  cross-layer consideration), `changes_since_last` diff. No LLM.
- `services/agents/dossier.py` - pulls the costar / matterport / vexcel layers through the adapters and
  merges them into a BuildingDossier. No LLM.
- `services/agents/cache.py` - prefetch cache under `data/cache/<building_id>/` (24 h, not committed).
- `data/fixtures/calls/` - scripted calls S1-S4. `data/fixtures/<building_id>/{costar,matterport,vexcel}.json`
  - per-layer fixtures, our own normalized fields only.
- `workspace/<incident_id>/` - incident files (incident.json, candidates.json, layers/*.json, dossier.json, brief_vN.json,
  critique_vN.json, todos.json = the orchestrator's plan + status for the UI progress strip, notes/*.md).
  This is the deepagent filesystem AND what the UI renders. Not committed.

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
- Add an `interrupt_on` gate before publishing a brief that has low-confidence considerations.
