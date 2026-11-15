# FlameScout

**FlameScout turns live fire-dispatch calls into a building-specific size-up on the crew's tablet before the engine arrives.**

HackPrinceton Fall 2026 · main track: Healthcare (public safety and emergency response) · **simulation only: nothing here calls a phone number or connects to a 911 line.**

## The problem

On arrival, a company officer spends minutes finding entries, stairs, occupancy and hazards. Pre-incident plans cover few buildings and go stale. What callers say is scattered across several calls and often disagrees ("third floor" vs "fourth floor").

## What it does

1. **Listens** to dispatch calls (scripted, typed, or a recorded call-in) and fuses every caller into one evolving incident record. Contradictions are flagged and never auto-resolved.
2. **Locates** the building (CoStar property search) and lets the dispatcher confirm it. Clear matches can auto-confirm.
3. **Pulls a dossier** from three layers for the same address: CoStar (use, size, tenants), Matterport (interior scan: rooms, doors, stairs) and Vexcel (aerial views, roof, neighbours).
4. **Synthesizes** a brief: size-up facts, hazard flags, exposures, up to five ranked *considerations* ("Consider ..."), gaps and a freshness banner. Every claim carries a source, a confidence and an evidence link, or is marked *unverified*.
5. **Plots routes** on the 3D and 2D plan: where callers report people, smoke and fire, and a computed entry-to-target route. If no route can be computed, the viewer says so; nothing is guessed.
6. **Updates live**: each new call produces a new brief version with a visible "changed since" line.

**Target users:** the company officer en route (the MDT view), the dispatcher on the call (the dispatcher console), and the incident commander (a shared picture with data freshness and confidence).

## Design rules

- **Advisory only.** The incident commander decides. Wording is "Consider ...", never an order.
- **Evidence-linked.** Every fact, marker and consideration has a status (`verified`, `inferred`, `reported`, `unverified`, `contradicted`), a confidence and evidence ids that resolve.
- **Deterministic first.** Distances, heights, counts, contradictions and **routes** are computed in code. Models only extract facts from one utterance, read images, draft considerations and critique. The agent never outputs coordinates or route geometry.
- **Abstain over guess.** A building without an interior scan shows "No interior scan on file" and a list of gaps, never invented interior data.

## Architecture

```
 call audio / typed lines / scripted calls
        |
        v
  extract (Gemini, quote-verified) -> fusion (code) -> IncidentRecord --WebSocket--> dispatcher console
        |                                                   |
        v                                                   v
  locator (CoStar) -> candidates -> dispatcher confirms (or auto-confirm)
        |
        v
  orchestrator: one persistent deep agent per incident (LangChain deepagents)
     |-- costar / matterport / vexcel subagents (run in parallel)
     |-- incident_mapper (reports -> places in the plan)
     |-- strategy (drafts considerations)  ->  critic (checks evidence, wording)
     v
  brief_vN.json + overlay_vN.json ---WebSocket---> MDT: 3D + 2D viewer, planner draws the routes
```

- **`packages/schemas/`**: Pydantic models, the contract between agents, API and UI.
- **`services/agents/`**: extraction, fusion, locator, dossier, brief assembly, the deepagents orchestrator, long-term building memory, the overlay.
- **`services/api/`**: FastAPI app (REST, WebSocket, metrics, audit log), call transcription, camera links.
- **`apps/web/`**: React + Vite dispatcher console and MDT, plus the house viewer (Three.js, no build step) and its Node data service.
- **`services/messenger/`**: a small Node service that sends the caller a camera link by iMessage (Photon Stable).

### How the agents are built (deepagents)

One orchestrator per incident, on its own checkpointed thread. It plans with `write_todos` (shown to the dispatcher as a progress strip), delegates to subagents through `task`, keeps its working state in an incident filesystem (the UI renders straight from these files), and **pauses for a human at `confirm_building`** and when a consideration has low confidence (`publish_brief`). Skills (`occupancy-hazards`, `size-up-format`) load on demand. Crew feedback is kept per building as long-term memory and applied to later briefs. Runs have a deadline: past it, the best partial brief is published. A second model provider can take over failed calls, and the critic can run on a different provider than the drafter.

## Sponsor technologies, and where they are used

| Technology | Use in FlameScout |
|---|---|
| **CoStar** | Property search and records (use, size, tenants, listing facts) for the locator and the dossier. The kit is normalized into a local index (`scripts/build_costar_index.py`). |
| **Matterport** | Live read-only floor plans from the Matterport API: rooms, doors, stairs. These drive the 3D and 2D viewer, the evacuation graph and the route planner. |
| **Vexcel** | Oblique aerial views in the viewer. Gemini vision reads the images (exposures, roof, entries, storeys); distances and geometry are then computed in code. |
| **LangChain deepagents + LangSmith** | The orchestrator described above, traced in LangSmith. |
| **Gemini** | Fact extraction, the agents and critic, aerial image reading and call-in transcription (structured output, quote-verified). OpenAI or xAI can replace it, and xAI is the failover here. |
| **Tiger Data (Postgres + TimescaleDB)** | Incidents, facts, call segments and pipeline timings as hypertables, a continuous aggregate, the `time_to_brief` view and the agents' checkpoints. Metrics at `/api/metrics/time-to-brief`. |
| **MongoDB Atlas** | The incident workspace the agents and the UI share, the prefetch cache and per-building memory. |
| **Vultr** | Deployment: Docker Compose (api, house-data, messenger) behind Caddy. See `docs/deploy-vultr.md`. |
| **ChatOverflow** | Our coding agents were connected to the ChatOverflow leaderboard while building. |
| **Photon (Stable)** | iMessage camera link to the caller. This uses Photon Stable (spectrum-ts), not Photon 2.0. |

## Run it

Requires Python 3.11+, Node 22+. Keys go in `.env` (gitignored; copy `.env.example`). Never commit keys.

```bash
# Backend (from the repo root)
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn services.api.main:app --port 8000                # docs at http://localhost:8000/docs

# Frontend
cd apps/web && npm ci && npm run dev                     # http://localhost:5173 (proxies /api and /ws to :8000)
```

Open the app, pick a scripted call in the top bar (for example **S2**: two callers, one contradiction) and press **Run call**. The Dispatcher view fills in; confirm the location (or let it auto-confirm); the MDT shows the plan, then the brief, then a new version when the second call arrives. You can also choose **Blank** and type transcript lines yourself: each caller line shows what the model extracted from it. The top bar shows **LIVE** when the API answers and falls back to **MOCK DATA** when it does not (`?mock=1` forces mock data).

Environment (see `.env.example`):

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_FAST_MODEL`, `GEMINI_VISION_MODEL` | Gemini (model IDs from AI Studio) |
| `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDER`, `CRITIC_PROVIDER` | `gemini`, `openai` or `xai`; failover and critic provider |
| `EXTRACTOR` | `gemini` (model) or `keyword` (offline stub; recognises only a few hazard words) |
| `MATTERPORT_API_KEY` + `MATTERPORT_API_SECRET`, `VEXCEL_API_KEY` | live plans and aerial views for the viewer |
| `COSTAR_INDEX` | path to the index built from the CoStar kit |
| `TIGER_DATABASE_URL`, `MONGODB_URI` | optional databases; unset means local files |
| `DATA_MODE` | `fixture` (demo never depends on live APIs) or `live` |

### Tests

```bash
pytest -q                          # offline
RUN_LIVE=1 pytest -q -m live       # live model and database tests
```

More: [docs/backend.md](docs/backend.md) (API), [docs/agents-api.md](docs/agents-api.md) (orchestrator), [docs/INCIDENT_OVERLAY_SPEC.md](docs/INCIDENT_OVERLAY_SPEC.md) (overlay and routes), [docs/LOCAL_DEMO.md](docs/LOCAL_DEMO.md) (viewer scenarios), [docs/deploy-vultr.md](docs/deploy-vultr.md) (deployment), [docs/PRD.md](docs/PRD.md) (full spec).

## Known limits

- Routes and distances are estimates from partial scans and caller reports. Room types come from a classifier and can be wrong. Verify on site.
- Interior data exists only for buildings with a Matterport scan; others show gaps.
- Some data (CoStar kit, raw API responses) is licensed and is not in the repository.
- A brief takes tens of seconds to build; it depends on model latency and rate limits.
- Everything is a simulation with synthetic calls. No real emergency line is ever contacted.
