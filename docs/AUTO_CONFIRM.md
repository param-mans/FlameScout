# Location auto-confirmation, call-script manifest, event shapes

## AUTO_CONFIRM_LOCATION

`AUTO_CONFIRM_LOCATION` (env, default `1`; `services/api/config.py` → `Settings.auto_confirm_location`).

- `1`: when a segment changes the reported location and the locator returns candidates, the service
  confirms by itself if there is **one candidate**, or the best has **score ≥ 0.8** and leads the second by
  **≥ 0.2** (`services/api/service.py: auto_confirm_pick`). It calls `IncidentService.confirm_location`, the
  same path as `POST /api/incidents/{id}/confirm-location`, so the events are identical; only
  `confirmed_by` differs. Audit row: role `system`, action `confirm_location`,
  detail `{"building_id", "confirmed_by": "auto"}`.
- `0`, or an ambiguous result (e.g. S3 "Harbor Plaza": two candidates at 1.0): no confirmation; the incident
  waits for the dispatcher (status `locating`, `confirmed_building_id` null).
- `POST /confirm-location` stays and always sets `confirmed_by = "dispatcher"` (also the simulated tap of
  `POST /api/calls/simulate {confirm_building_id}`; simulate skips its tap if auto already confirmed that
  building). A dispatcher may still re-confirm a different building after an auto-confirm.

`confirmed_by` is stored in `<workspace>/_api/<incident>/confirmation.json` (the shared schema
`IncidentRecord` has no such field) and added to the incident JSON by the API.

## Which id `confirmed_building_id` holds

The building id of the locator candidate. On the default orchestrator pipeline, for the residential hero
houses this is the **CoStar residential listing id** (= folder name in `data/raw/<id>/`):
S6/S6_FAIRDALE → `209217448`, S7_FAIRHOPE_OAK → `214905148`, S8_PLAYA_LUCIA → `199958956`
(the commercial hero S9_NWTC/S4B → CoStar property id `23296`; fixtures → folder names such as
`fake_wilshire_tower`). It comes from `services/agents/providers.py` (`raw_property` → `_residential`,
`property_id = listing id`). This requires `data/raw/` locally; PIPELINE=rules does not read `data/raw`.

## Call-script manifest

`data/fixtures/calls/manifest.json`: `{"scripts": [{"id", "title", "group": "showcase"|"test", "house",
"inconsistent", "visible"}]}`. `GET /api/calls/scripts`:

- no manifest: every `*.json` script (old behaviour), items `{script_id, description, segments}`;
- with manifest: only entries with `visible` true and an existing file, showcase first then test (manifest
  order within a group); each item `{script_id, description, segments, title, group, inconsistent, house}`.

`manifest.json` is never listed nor loadable as a script. `POST /api/calls/simulate` accepts any script id
with a file, listed or not.

## Incident JSON (GET /api/incidents/{id}, GET /api/incidents, incident.updated)

Keys: `incident_id, status, call_ids, facts, evidence, contradictions, open_questions, confirmed_building_id,
confirmed_by, ready_to_brief, created_at`.

- `status`: `listening` (no location yet) → `locating` (location reported; **awaiting manual confirmation**
  when `confirmed_building_id` is null and candidates exist) → `briefing` (confirmed, brief in progress) →
  `confirmed` (brief v1 out) → `updated` (v2+).
- `confirmed_building_id`: null until confirmed. `confirmed_by`: `"auto"` | `"dispatcher"` | null.
- Candidates are not in the incident: `GET /api/incidents/{id}/candidates` or the `location.candidates` event.
  Each candidate: `{building_id, name, address, coordinates: {lat, lon}, score, match_reasons, scan_available}`.

## WebSocket events (/ws/incidents/{id})

Envelope: `{type, incident_id, ts, trace_id, payload}`.

| type | payload |
|---|---|
| `incident.updated` | `{incident: <incident JSON above>}`; the first message on connect also has top-level `snapshot: true` |
| `transcript.segment` | `{segment, changes, extracted}` |
| `location.candidates` | `{candidates: [candidate]}` (no `location.confirmed` event: watch `incident.updated` with `status` `briefing` and `confirmed_by`) |
| `dossier.progress` | orchestrator: `{incident_id, status, version, todos, candidates, trace_run_id, updated_at}`; status `running`/`awaiting_confirmation`/`done`/`rejected`/... Note `awaiting_confirmation` is the agent's internal pause and is also emitted after an auto-confirm; use the incident, not this, for the UI |
| `brief.updated` | `{version, status, published, needs_review, fallback_reason, pipeline, trace_run_id, changes_since_last, brief}` |
| `overlay.updated` | `{version, overlay}`; right after the `brief.updated` of the same version (not sent if no valid overlay file) |
| `contradiction.raised`, `copilot.suggestion`, `metric.tick` | unchanged |

Order after a confirmation (auto or manual): `incident.updated` (status `briefing`) … `brief.updated` →
`overlay.updated` (same version) → `incident.updated` (status `confirmed`/`updated`) → `metric.tick` (v1).

## Late clients

- The WebSocket does **not** replay past events: it sends one `incident.updated` snapshot, then live events.
- Latest brief: `GET /api/incidents/{id}/briefs/latest`. Latest overlay:
  `GET /api/incidents/{id}/overlay/latest` → the last `overlay.updated` payload `{version, overlay}` (404 if none).
