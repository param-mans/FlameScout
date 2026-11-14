# FlameScout

FlameScout turns live fire-dispatch calls into a building-specific size-up on the crew's MDT
before the engine arrives. HackPrinceton Fall 2026 (main track: Healthcare). Submission deadline:
8:00 AM Sunday Nov 15 (hard). Full spec: docs/PRD.md. When the PRD and this file disagree, this file wins.

## Commands
- Install: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Tests: `pytest -q`
- Replay a call script through fusion: `python scripts/run_fusion.py S2`
- Export JSON Schema for the frontend: `python scripts/export_schemas.py`
- API: `uvicorn services.api.main:app --reload --port 8000` (docs at /docs, WS at /ws/incidents/{id})
- Tiger Data schema (once): `python scripts/init_tiger.py`
- Real CoStar data (once): `python scripts/build_costar_index.py ~/Downloads/costar-hackathon-kit`
  -> `data/costar/properties.jsonl` (gitignored, licensed); set `COSTAR_INDEX` in `.env`.

## Layout
- `packages/schemas/flamescout_schemas/` - Pydantic models. The contract between agents, API and UI.
  Change a model -> run export_schemas.py and tell the frontend person.
- `services/agents/extract.py` - per-utterance fact extraction (Keyword + Gemini, quote-verified).
- `services/agents/fusion.py` - deterministic merge, contradictions, open questions. No LLM.
- `services/agents/{locator,dossier,brief,critic,copilot}.py` - plain-function pipeline stages.
- `services/agents/tools/` - provider adapters (+ fixtures, live fallback), geocode, rules (R-01..R-09), vision.
  `costar_kit.py` normalizes the CoStar hackathon kit (3,339 Houston/LA properties); with
  `COSTAR_INDEX` set it serves CoStar in every mode, and the synthetic fixture buildings still resolve.
- `services/api/` - FastAPI app, IncidentService, JSON workspace store, event bus, metrics/Tiger.
- `config/rules.yaml` - rule thresholds (placeholders, not operational standards).
- `data/fixtures/calls/` - scripted calls S1-S4. `data/fixtures/<building_id>/` - dossier fixtures.
- `workspace/<incident_id>/` - incident files (incident.json, dossier.json, brief_vN.json).
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
- Timebox: if a feature isn't working in its estimate, fall back to the fixture and move on.
