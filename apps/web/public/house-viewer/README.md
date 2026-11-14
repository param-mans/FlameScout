# House viewer (static, embeddable)

2D floor plan and 3D model of a house with an evacuation distance heat map, built from a floor plan
generated from the Matterport API. Plain HTML + ES modules, no build step, no server code, no network
calls outside this folder. Vite serves it as-is from `public/`, so it is available at
`/house-viewer/casa.html` in `npm run dev` and in the production build.

## Files

| File | Role |
| --- | --- |
| `casa.html`, `casa.js` | Viewer (3D scene, 2D SVG plan, panels, embed mode, postMessage API) |
| `theme.js`, `theme.css` | Monochrome theme. Colors can be overridden with CSS variables (see below) |
| `evac.js` | Evacuation graph: rooms linked by doors, exits on the lowest floor, floors linked by staircases |
| `escaleras.js` | Stair pairing between floors, climb direction and footprint fit |
| `samples/` | Four normalized sample houses + `index.json` |
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

## Samples

| Id | Floors | Rooms* | Notes |
| --- | --- | --- | --- |
| `sample-a` | 2 | 19 | One flight of stairs; climb direction clear; footprint fits at 100 % |
| `sample-b` | 2 | 27 | One flight of stairs; climb direction clear; footprint fits at 100 % |
| `sample-c` | 1 | 17 | |
| `sample-d` | 1 | 15 | |

`samples/index.json` lists each sample with a **fictitious** address (`address`) for the house picker,
plus its title, floors and rooms.

\* Rooms other than Void.

All four passed the house quality check and have at least 4 windows. Every room polygon the viewer
draws is `polygonQuality: "ok"`. The 2-floor samples contain a few `Void` areas with an unreliable
outline. Void areas are never drawn and are not used for evacuation or stairs.

Each file is `{ id, title, quality, stairs, plan }`:

- `plan`: floors, walls, openings and rooms in local meters (origin inside the house).
- `quality`: result of the quality check (`estado`, `etiquetas`, `motivo`, counts).
- `stairs`: precomputed stair analysis (`connected` per pair of floors, `pairs` with state, scale,
  direction and reason).

The viewer recomputes stairs and evacuation at load time with the same modules. All
identifying data was removed: model id, address, coordinates, listing and agent data. Matterport
element ids were replaced with short opaque ids (`f1`, `r1`, `w1`, `o1`).

## Oblique views (Vexcel)

Under the 2D plan the viewer shows the Vexcel oblique aerial views (N / E / S / W = the direction the camera
faces; the straight-down view is left out on purpose). Sources, in order:

1. **API**: `GET /api/house-viewer/vexcel/<sample>` and `.../<sample>/oblique-<dir>.jpg`, same origin. In
   `npm run dev`, Vite forwards `/api` to `localhost:8000`, where the imagery server runs (demo_costar
   `tools/serve.py` with `VEXCEL_API_KEY` in its environment). The server maps the anonymous sample id to the
   house, calls Vexcel with the key in the `X-API-KEY` header and caches the images. It returns only the
   capture date and copyright: no key, coordinates, model id or request parameters reach the browser.
2. **Local copy**: `local-imagery/<sample>/` (`oblique-<dir>.jpg` + `meta.json`), kept out of git by its own
   `.gitignore`.

Without either, the section explains what is missing (server not reachable, key not set...). The images are
never committed: Vexcel's license, and a photo of the real house would reveal where the anonymized sample is.

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
