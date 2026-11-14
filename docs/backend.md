# FlameScout backend

FastAPI service that turns simulated dispatch calls into versioned, evidence-linked briefs.
**Simulation only**: nothing here calls a phone number or connects to a 911 line.

```bash
source .venv/bin/activate && pip install -e ".[dev]"
uvicorn services.api.main:app --reload --port 8000     # OpenAPI docs: http://localhost:8000/docs
pytest -q
```

## Pipeline (all plain functions; the deepagents orchestrator can call them as tools)

```
segment -> extract (keyword | Gemini, quote-verified) -> fusion -> IncidentRecord
        -> locator (CoStar search [+ Nominatim]) -> candidates -> dispatcher confirm
        -> dossier (CoStar, then Matterport || Vexcel [+ Gemini vision]) -> rules R-01..R-09
        -> brief (rule-templated "Consider ..." + deterministic critic) -> brief_vN.json
```

## REST

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | data mode, extractor, Gemini/Tiger/geocoder status, recent live fallbacks |
| POST/GET | `/api/incidents` | create `{incident_id?}` / list |
| GET/DELETE | `/api/incidents/{id}` | |
| POST | `/api/incidents/{id}/segments` | one `CallSegment` (STT or text feed) |
| GET | `/api/calls/scripts` | S1-S4 |
| POST | `/api/calls/simulate` | `{script_id, incident_id?, realtime?, speed?, confirm_building_id?, confirm_after_segment?}` |
| GET | `/api/incidents/{id}/candidates?refresh=` | ranked `LocationCandidate`s |
| POST | `/api/incidents/{id}/confirm-location` | `{building_id}` -> new `Brief` (only way to get a brief) |
| POST | `/api/incidents/{id}/contradictions/resolve` | `{key, value}` |
| GET | `/api/buildings`, `/api/buildings/{id}/dossier?refresh=` | |
| GET | `/api/incidents/{id}/briefs`, `/briefs/latest`, `/briefs/{version}` | views are audited |
| GET | `/api/incidents/{id}/copilot?include_closed=` | |
| POST | `/api/copilot/suggestion/{sid}/dismiss` and `/asked` | |
| POST/GET | `/api/incidents/{id}/feedback` | `{fact_key, verdict: correct/wrong/outdated, note?}` |
| GET | `/api/incidents/{id}/audit` | |
| GET | `/api/metrics/time-to-brief`, `/api/metrics/stages` | Tiger Data if configured, else workspace JSONL |
| POST | `/api/admin/data-mode` | `{mode: live/fixture}`, needs `X-Role: admin` |

Header `X-Role: dispatcher|officer|admin` (demo role flag, recorded in the audit log).

## WebSocket `/ws/incidents/{id}`

First message is an `incident.updated` snapshot (`"snapshot": true`). Then `Event` envelopes
`{type, incident_id, ts, trace_id, payload}` with types `transcript.segment`, `incident.updated`,
`contradiction.raised`, `location.candidates`, `dossier.progress`, `brief.updated` (full brief in
payload), `copilot.suggestion`, `metric.tick`, `overlay.updated` (`{version, overlay}` right after
`brief.updated`, only when `overlay_vN.json` exists for that version; docs/INCIDENT_OVERLAY_SPEC.md).

## Third-party integrations

| Integration | Status | Switch |
|---|---|---|
| Gemini extraction (`langchain-google-genai`, structured output, retry) | implemented; output gated in code (known key, verbatim quote) | `GEMINI_API_KEY` + `GEMINI_FAST_MODEL` |
| Gemini vision (stairs/exits/roof/adjacent labels; cached per image) | implemented; runs only on items without detections and with a real image path/URL | `GEMINI_VISION_MODEL` |
| Nominatim geocoder (boosts candidates within 150 m) | implemented, 1 req/s, cached | `GEOCODER=nominatim` |
| Tiger Data (hypertables + continuous aggregate) | implemented, async writes, backs off on error | `TIGER_DATABASE_URL`, then `python scripts/init_tiger.py` |
| CoStar / Matterport / Vexcel | **adapter + fixture only**. Live classes refuse until field names are confirmed (PRD 20), and fall back to fixtures; misses show in `/health` | `DATA_MODE=live` + keys |
| LangSmith | picked up from env by langchain calls | `LANGSMITH_*` |

To wire a vendor: implement the methods in `services/agents/tools/providers.py` (`Live*Provider`),
mapping the real response into the normalized shapes documented at the top of that file.

## Fixtures

`data/fixtures/<building_id>/{costar,matterport,vexcel}.json` are **synthetic placeholders** in the
normalized adapter shape (each file says so in `_fixture`). Replace them with real toolkit data
after the smoke test. Buildings: `wilshire_tower` (S1/S2, scanned), `harbor_plaza_ocean` +
`harbor_plaza_pine` (S3 ambiguity), `maple_lofts` (S4, no scan).
