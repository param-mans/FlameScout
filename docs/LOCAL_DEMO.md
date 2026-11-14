# Local demo: incident layer on the house viewer

Everything runs from the web app's dev server. There is no other process: the Vite plugin
`apps/web/server/house-data.mjs` serves the plan and imagery routes.

## Start

```bash
cd apps/web
npm ci            # once
npm run dev       # Node 22 (e.g. PATH=~/.nvm/versions/node/v22.23.2/bin:$PATH npm run dev)
```

Open `http://localhost:5173/`. The MDT shows the house viewer by default (`?viewer=0` brings back the old tabs).

## Environment (repo `.env`, gitignored)

| Variable | Used for | If it is missing |
| --- | --- | --- |
| `MATTERPORT_API_KEY` + `MATTERPORT_API_SECRET` (or `MP_ID` + `MP_SECRET`) | Live floor plans from the Matterport API (read-only) | The star buildings (209217448, 214905148, 199958956) are served from their stored API responses in `data/raw/<id>/matterport/raw_floors.json`. The samples a–d show "Viewer unavailable: Matterport API token is not set and no stored plan exists". |
| `VEXCEL_API_KEY` | Oblique aerial views under the 2D plan | The section says "VEXCEL_API_KEY is not set"; the rest works. |

`.env` is read only when the server starts: restart `npm run dev` after editing it. 23296 (no scan) needs no key,
except for its aerial views.

## URLs

`debug=1` shows the DEBUG chip (bottom left) with the scenario buttons. `scenario` and `v` load a scenario directly.
In the app these parameters are forwarded to the viewer iframe.

| What | URL |
| --- | --- |
| Viewer alone, debug | `http://localhost:5173/house-viewer/casa.html?debug=1&sample=209217448` |
| App (MDT), debug | `http://localhost:5173/?debug=1&sample=209217448` |
| S1, one caller (sample house) | `http://localhost:5173/?debug=1&sample=sample-b&scenario=S1` |
| S2 v1, first caller | `http://localhost:5173/?debug=1&sample=sample-b&scenario=S2&v=1` |
| S2 v2, contradicting callers | `http://localhost:5173/?debug=1&sample=sample-b&scenario=S2&v=2` |
| S3, floor-only locations | `http://localhost:5173/?debug=1&sample=sample-b&scenario=S3` |
| S4 v1, Fairdale, neighbour's call | `http://localhost:5173/?debug=1&sample=209217448&scenario=S4&v=1` |
| S4 v2, Fairdale, mother's call | `http://localhost:5173/?debug=1&sample=209217448&scenario=S4&v=2` |
| 23296, no interior scan | `http://localhost:5173/?debug=1&sample=23296&scenario=S5` |

Every URL also works in the standalone viewer: replace `/?` with `/house-viewer/casa.html?`. S1–S3 work on any
scanned house; they are built from that house's own room ids. S4 is only offered on Fairdale (209217448).

## Demo steps (Fairdale)

1. Open S4 v1. The banner shows incident `inc-s4` v1 and, in green, "Occupants reported on floor 2 (room unknown)".
   - The kitchen has a grey-red smoke haze, labelled "SMOKE (reported)".
   - Floor 2 has a green tint and its label.
   - There are two dashed white routes: a rescue route to floor 2 by the stairs in `plan.json`, and a recon route to the
     kitchen. Both are numbered, labelled "estimate", and the recon one is tinted red where it passes near the fire.
2. Click **Next call → v2** in the DEBUG chip, or open S4 v2. The banner highlights the four changes since v1 (amber).
   - The children are now contradicted between floor 2 and floor 1: dashed green and "?", plus a "Contradiction" notice.
   - The husband shows as a figure in the office: green glow, beacon, and the label "1 reported · min ago · call-2".
   - Flames show at a kitchen window: a red gradient and heat column, labelled "FIRE (reported)".
   - A low-confidence gas hazard has an amber icon in the garage.
   - The call 1 smoke turns "stale", because it is more than 10 minutes older than `as_of`.
   - The rescue to the office is priority 1. There is one dashed route per children candidate, plus the garage recon.
3. Use the **v1 / v2** buttons in the banner to compare the versions received.
4. Click a card under **Considerations · incident**: its refs are outlined in white in both views. Click it again to
   clear.
5. Open 23296. It shows "No interior scan on file", banners and the considerations panel, and never a plan or a route.

Everything shown is an estimate from caller reports and a partial scan. It is advisory only, and the incident
commander decides. The demo is a simulation, never connected to real emergency lines.

## Without the UI: send an overlay

The host sends `postMessage({ type: 'setIncident', overlay }, origin)` to the viewer iframe. The viewer validates the
overlay with `incident/validate.js` against its plan index, and the planner replaces any routes. It answers
`incidentApplied` or `incidentRejected`. See `docs/INCIDENT_OVERLAY_SPEC.md`.

Tests:

- `node apps/web/server/tests/validate.test.mjs`
- `node apps/web/server/tests/plan-index.test.mjs`; set `LIVE=1` to use the live API.
- `python3 tests/test_validate_overlay.py`
