# House viewer (static, embeddable)

2D floor plan and 3D model of a house with an evacuation distance heat map, built from a floor plan
generated live from the Matterport API. Plain HTML + ES modules, no build step. House data comes from
same-origin API routes served by the Vite plugin `apps/web/server/house-data.mjs` (see Data). Vite serves it as-is from `public/`, so it is available at
`/house-viewer/casa.html` in `npm run dev` and in the production build.

## Files

| File | Role |
| --- | --- |
| `casa.html`, `casa.js` | Viewer (3D scene, 2D SVG plan, panels, embed mode, postMessage API) |
| `theme.js`, `theme.css` | Monochrome theme. Colors can be overridden with CSS variables (see below) |
| `evac.js` | Evacuation graph: rooms linked by doors, exits on the lowest floor, floors linked by staircases |
| `escaleras.js` | Stair pairing between floors, climb direction and footprint fit |
| `samples/index.json` | House catalog: address, coordinates, Matterport model id, listing facts |
| `vendor/three/` | Three.js r160 (MIT, `vendor/three/LICENSE`): `build/three.module.js` and the addons used |

`vendor/three` holds only the files the viewer imports. The one change from the npm package
`three@0.160.0` is that the addons' `from 'three'` imports were rewritten as relative paths
(`../../../build/three.module.js`). Vite's dependency scan also reads HTML files under `public/`, and
an unresolved bare `three` import makes it skip pre-bundling for the whole app.

## URL parameters

| Parameter | Values | Effect |
| --- | --- | --- |
| `sample` | `sample-a` ... `sample-d` | House to load (default: first entry of `samples/index.json`) |
| `embed` | `1` | Single full-size view for an iframe: no header, side panels or entity table |
| `mode` | `2d` \| `3d` | View shown in embed mode (default `3d`) |
| `layout` | `split` | Embed mode: 3D (left, about two thirds) and 2D plan side by side in one page, stacked when the frame is narrower than 900 px. Shared toolbar (floor, rooms / heat map, fit, collapsible Layers menu); right column with the plan, legend, collapsible inspector and collapsed entity table. Selection and hover are shared by both views |
| `huella` | `original` | Diagnostic: draw stair footprints without the fit step |

Without `embed=1` the page shows the full viewer: view, layer and room-type controls, both views and
the entity table.

## Data: everything from the APIs

The viewer stores no house data. Only the catalog `samples/index.json` is in the repo: for each house, its real
address, city, market, coordinates (`location`), Matterport model id and listing facts (beds, baths, sq ft, year
built, type). Everything else is generated at request time by the Vite server through the plugin
`apps/web/server/house-data.mjs`, running in `npm run dev` and `npm run preview` with no extra process:

| Route | Source | What it returns |
| --- | --- | --- |
| `GET /api/house-viewer/sample/<id>` | Matterport GraphQL API | `{ id, address, location, matterportModelId, listing, quality, stairs, plan }` |
| `GET /api/house-viewer/vexcel/<id>` | Vexcel API 2.0 | oblique views `{ views: { dir: { file, captureDate } }, copyright }` |
| `GET /api/house-viewer/vexcel/<id>/oblique-<dir>.jpg` | Vexcel API 2.0 | JPEG |

- **`plan`**: `server/matterport-plan.mjs`, a Node port of the demo's `tools/mp_plan.py`. It sends the same
  read-only query (floors, edges with openings, rooms with boundary, holes, classifications and metric
  dimensions; never a mutation). It then runs the same room-outline graph assembly and validation, and
  reproduces Python's numbers (`server/py.mjs`: round half to even on the exact value, float modulo).
- **`quality`**: `server/house-quality.mjs`, a port of the demo's `tools/calidad.py`, with the same criteria
  and texts. The listing sq ft and beds come from the catalog. The vertical-connection criterion uses the
  same stairs module the viewer draws with. Checked against the demo's results for all 1,463 houses:
  identical.
- **`stairs`**: stair analysis of `escaleras.js`, with pairs, fit state, scale, direction and reason.

Results are cached in memory for 6 hours per server process; nothing is written to disk. Credentials are read
on the server from the repo's `.env` (gitignored) and never reach the browser:

- `VEXCEL_API_KEY`;
- the Matterport API token as `MATTERPORT_API_KEY` (token id) plus `MATTERPORT_API_SECRET` (token secret),
  or `MATTERPORT_API_KEY=<id>:<secret>`.

A static deploy without the Vite server has no house data until the backend serves these routes.

## Oblique views (Vexcel)

Under the 2D plan the viewer shows the Vexcel oblique aerial views (N / E / S / W = the direction the camera
faces; the straight-down view is left out on purpose), from `/api/house-viewer/vexcel/<id>`. The key travels
only in the `X-API-KEY` header. Vexcel imagery is licensed: it is never stored or committed.

## postMessage API

Messages are accepted only from the same origin and only from `window.parent`. Outgoing messages
are posted to the same origin.

Into the viewer (the page posts to the iframe's `contentWindow`):

```js
{ type: 'focusFloor', floor: 'all' }              // or 1..n
{ type: 'highlight', id: 'r3' }                   // room, door or window id; id: null clears
{ type: 'setLayer', layer: 'heatmap', on: true }  // walls | doors | windows | slabs | stairs | labels | route | heatmap
{ type: 'loadSample', id: 'sample-c' }            // load another house without reloading the page
{ type: 'theme', vars: { '--color-bg': '#0a0d11', '--font-mono': '…' } } // app design tokens, see Theme
```

Out of the viewer:

```js
{ type: 'ready', sample: 'sample-a', floors: 2, mode: '3d' }
{ type: 'select', id: 'r3', kind: 'room', floor: 1 }   // user click in 2D or 3D; id: null when cleared
{ type: 'error', message: 'Unknown sample: x' }
{ type: 'themeApplied' }                                // reply to 'theme'
```

## Theme

Without a theme message the viewer uses its own monochrome theme. The default can be tuned with
optional CSS variables on `:root`: `--hv-hue` (palette hue, default 205) and `--hv-<key>` for any
color key of `T` in `theme.js`.

`BuildingViewer` (apps/web) reads the app's design tokens (`--color-bg`, `--color-panel`,
`--color-panel-hi`, `--color-line`, `--color-line-hi`, `--color-ink`, `--color-dim`, `--color-faint`,
`--color-accent`, `--color-verified`, `--color-inferred`, `--color-unverified`, `--font-mono`,
`--font-sans`). It sends them with `{type:'theme', vars}` on the first `ready` and keeps the iframe
hidden until `themeApplied` arrives. The viewer then:

- uses a flat background equal to the column, and the app's panels, borders and fonts;
- styles buttons like the app chips (accent when active);
- shows status chips (● VER, ◆ INF, ○ UNV) in the app's status colors;
- takes the hue of `--color-accent` as the palette hue, so walls, slabs, doors and heat map keep
  their monochrome hierarchy.

Unknown keys and values containing `; { } < >` are ignored.

## Limits

Distances are straight lines between room centers and doors: an estimate that ignores furniture.
The top floor's wall height (2.7 m) is an estimate. Room types come from an AI classifier and can
be wrong. Everything is advisory: verify on site.
