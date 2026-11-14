# Live incident overlay: backend → web app → viewer

## Event flow

1. The orchestrator writes `overlay_vN.json` for every brief version: the incident mapper's overlay, or an empty
   fallback.
2. The API (`services/api/service.py`, `_emit_overlay`) publishes `overlay.updated` right after `brief.updated`. Both
   events have the same version and trace id.
3. The web app's main MDT viewer (`BuildingViewer` with `layout="split"`), only with `?incident=<id>`, listens on
   `/ws/incidents/<id>` through `apps/web/src/house/useIncidentSocket.ts`. Vite proxies `/ws` to the API on :8000.
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

The mock is dev only and not deployed. In terminal 1, from the repo root:

```sh
~/.venvs/flamescout/bin/python scripts/mock_incident_ws.py --port 8000   # Fairdale S4: v1 after 1 s, v2 after 6 s
```

It accepts `--invalid` (adds a v3 the viewer rejects) and `--unknown` (sends a model_id that matches no house).

In terminal 2:

```sh
cd apps/web && npm run dev
```

Then open `http://localhost:5173/?viewer=1&incident=demo`.

The mock's overlays come from the viewer's debug scenario S4. To regenerate them, run
`node apps/web/scripts/export-fairdale-overlays.mjs` (needs `data/raw`).

## End-to-end test

```sh
cd apps/web && npm i --no-save playwright && npx playwright install webkit
node scripts/overlay-e2e.mjs            # add --shots <folder outside the repo> for screenshots
```

The test starts the mock and its own offline Vite by itself. That Vite serves stored plans from `data/raw`, has a
Vexcel test stub and calls no external API.

It runs in WebKit with touch at 1180×820, and also in Chromium if it is installed. It checks:
- the chip shows `LIVE · v1` then `LIVE · v2`, `data-incident-version` follows, and the house switches to Fairdale;
- with `--invalid` the chip shows `REJECTED v3 (keeping v2)`;
- with `--unknown` the chip shows `Unknown building` and nothing is applied;
- no WebSocket opens without `?incident=`;
- the console is clean;
- `scripts/ipad-webkit.mjs` still passes.

## Deployment

The deployed site needs Caddy to forward `/ws/*`, with WebSocket upgrade, to the API. That is not part of this change.
