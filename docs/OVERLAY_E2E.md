# Live incident overlay: backend → web app → viewer

## Event flow

1. The orchestrator writes `overlay_vN.json` for every brief version: the incident mapper's overlay, or an empty
   fallback.
2. The API (`services/api/service.py`, `_emit_overlay`) publishes `overlay.updated` right after `brief.updated`. Both
   events have the same version and trace id.
3. The app creates the incident on Run call (`POST /api/incidents`, id kept in localStorage `flamescout.incident`) and
   listens on `/ws/incidents/<id>` (`src/api/useLiveIncident.ts`). The MDT viewer gets `overlay.updated` from that socket
   (`house/liveFeed.ts`), no second socket; its own socket is only for `?incident=<id>` with the API down.
4. When an overlay arrives, the viewer loads that overlay's house, the app waits until the viewer reports the house
   `ready`, and then posts `{type: 'setIncident', overlay}` to the iframe.
5. The viewer validates the overlay against the plan (`incident/validate.js`) and draws it. It answers
   `incidentApplied` or `incidentRejected`, and the app shows that in the chip.

The real `overlay.updated` message (the API's `Event` envelope):

```json
{"type": "overlay.updated", "incident_id": "…", "ts": "…", "trace_id": "…",
 "payload": {"version": 2, "overlay": {"incident_id": "…", "model_id": "…", "version": 2, "…": "…"}}}
```

`docs/agents-api.md` describes a flat `{incident_id, ts, trace_id, version, overlay}` sent early from `on_overlay`. The
API does not use `on_overlay`. `overlayFromEvent()` in the hook accepts both shapes, in one place.

## URL parameters

| Parameter | Effect |
|---|---|
| `incident=<id>` | Turns the live overlay on (`[A-Za-z0-9_-]`, up to 64 characters). Without it the app opens no WebSocket and behaves as before. |
| `ws=<url>` | Optional, for tests: another WebSocket URL, accepted only on the page's hostname. |

Chip (44 px tall):
- `LIVE · connecting…`, `LIVE · waiting for overlay` or `LIVE · offline, retrying`
- `LIVE · vN` once the viewer applies version N
- `REJECTED vN (keeping vM)` when the viewer rejects one
- `Unknown building: <model_id>`

In live mode the chip sits in a 44 px row above the viewer. The container carries `data-incident-version`, the version
the viewer applied, and `data-live-sample`, the house shown. `?incident=` is also read by the dashboard's own live
client (`src/api/useLiveIncident.ts`), which opens its own socket when the API is reachable. Rejections are logged to the
console. The app reconnects after 1, 2, 4, 5, 5… s, and ignores events that are not `overlay.updated` as well as
malformed messages.

## Which house

`overlay.model_id` is looked up in the viewer catalog (`public/house-viewer/samples/index.json`):
1. first by Matterport model id (`matterportModelId`, e.g. `tvaSot6bAt8` for Fairdale);
2. then by catalog id (a listing id such as `209217448`, or a sample id such as `sample-a`).

With no match, nothing is applied and the chip says `Unknown building: <model_id>`.

## Demo with the mock (no backend, no keys)

`scripts/mock_incident_ws.py` is dev only and not deployed. Besides `/ws/incidents/<id>` it answers the minimal REST the
app calls at startup and on Run call: `GET /api/calls/scripts` (from `data/fixtures/calls/manifest.json`),
`POST /api/incidents` (new id), `GET /api/incidents/<id>` (any id: `confirmed_building_id` 209217448 = Fairdale,
`confirmed_by: "auto"`, status `briefing`), `POST /api/calls/simulate` (202, replays nothing), `candidates`/`copilot`
(`[]`) and `briefs/latest`/`overlay/latest` (404, as the real API before the first brief). On connect the socket sends
the `incident.updated` snapshot with the confirmed fields, then Fairdale S4 v1 (1 s) and v2 (6 s).

Flags: `--invalid` (adds a v3 the viewer rejects), `--unknown` (model_id in no catalog; the house stays the confirmed one),
`--off-catalog` (`confirmed_building_id` 99999999: the MDT shows "No interior scan on file").

```sh
~/.venvs/flamescout/bin/python scripts/mock_incident_ws.py --port 8000   # terminal 1, repo root
cd apps/web && npm run dev                                                # terminal 2
```

Open `http://localhost:5173/` and press Run call (or `/?incident=demo`). The overlays come from the viewer's debug
scenario S4; regenerate with `node apps/web/scripts/export-fairdale-overlays.mjs` (needs `data/raw`).

## End-to-end test

```sh
cd apps/web && npm i --no-save playwright && npx playwright install webkit
node scripts/overlay-e2e.mjs            # add --shots <folder outside the repo> for screenshots
```

The test starts the mock and its own offline Vite (proxies `/api/incidents`, `/api/calls` and `/ws` to the mock; plans
from `data/raw`, Vexcel test stub, no external API). WebKit with touch at 1180×820, plus Chromium if installed:
- app flow: Run call creates the incident (id in localStorage), the Fairdale plan shows before any overlay, then
  `LIVE · v1` / `LIVE · v2`; New call moves the one socket to the new incident and its overlays show;
  `--invalid` → `REJECTED v3 (keeping v2)`; `--off-catalog` → "No interior scan on file"; `?mock=tower` → no socket;
- explicit `?incident=demo`: v1/v2 and house switch, `--unknown` → `Unknown building`, no socket without an incident;
- console clean, except the expected rejection log and the 404s of `briefs/latest` / `overlay/latest` (the real API
  answers 404 too before the first brief);
- `scripts/ipad-webkit.mjs` runs afterwards with the mock stopped (app in mock mode). It already fails on origin/main
  (small touch targets), so its FAIL count is printed, not counted; compare with origin/main.

## Deployment

The deployed site needs Caddy to forward `/ws/*`, with WebSocket upgrade, to the API. That is not part of this change.

### Call in and Clear call

The mock also answers `POST /api/calls/web/callin` (a call from another device: a new incident on `/ws/calls`) and
`POST /api/calls/web/clear` (`calls.cleared`). Case g: Clear call clears the overlay and the stored incident, then a Call
in moves the app and the viewer to the new incident (one incident socket, its v1 and v2). The socket checks count only
`/ws/incidents/*`: the Call in feed (`/ws/calls`) opens on every screen, `?mock=tower` included, as on origin/main, and its StrictMode console messages (also on origin/main) are not judged.

