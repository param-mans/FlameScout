// Floor plan (uniform JSON) from the Matterport GraphQL API. Node port of the demo's tools/mp_plan.py: same queries,
// same room-outline graph assembly, same validation and the same numbers (Python rounding, see py.mjs).
//
// READ-ONLY: only GraphQL queries are sent. Mutations are refused (unlockModelBundle and others buy add-ons and are billed).
// Credentials: Matterport API token (id + secret, HTTP Basic), from the server environment / repo .env only. They are never
// written to files, logs, responses or error messages.
// Single source: floors.edges (walls with thickness and openings) + rooms (boundary, label, detected type, area). If the
// API fails or returns incomplete data (GraphQL errors, no floors or no edges) a MPError is thrown: there is no fallback.
// Results are kept in memory only (no files on disk).
import { pyRound as _p, pySum, pyMod, pyFixed, dist } from "./py.mjs";

const API = "https://api.matterport.com/api/models/graph";
export const ID_RE = /^[A-Za-z0-9]{6,24}$/;

const EDGE = "id type thickness centerLineBias vertices{ id position{ x y } } openings{ id type width height lowerElevation relativeCenter label }";
const REGION = "edges{ type vertices{ id position{ x y } } }";
// A query, never a mutation
const Q_FLOORS = `query($id:ID!){ model(id:$id){ id floors{ id label sequence edges{ ${EDGE} }
  rooms{ id label classifications{ label confidence } dimensionEstimates(units: metric){ area width height units }
         boundary{ ${REGION} } holes{ ${REGION} } } } } }`;

export class MPError extends Error {
  constructor(message, status = null, retryAfter = null) { super(message); this.status = status; this.retryAfter = retryAfter; }
}

const sleep = (s) => new Promise((ok) => setTimeout(ok, s * 1000));

/** Read-only GraphQL query. Retries with backoff on 429. `auth` is the Basic Authorization header value. */
async function gql(query, variables, auth, attempts = 4) {
  if (/\bmutation\b|unlock/i.test(query)) throw new MPError("mutations are not allowed");
  if (!auth) throw new MPError("Matterport API token is not set (MATTERPORT_API_KEY and MATTERPORT_API_SECRET in .env)", 503);
  const body = JSON.stringify({ query, variables });
  let wait = 2;
  for (let i = 0; i < attempts; i++) {
    let r;
    try {
      r = await fetch(API, { method: "POST", body, headers: { "Content-Type": "application/json", Authorization: auth }, signal: AbortSignal.timeout(60000) });
    } catch (e) {
      throw new MPError(`Cannot reach the Matterport API (${e.name})`);
    }
    if (r.ok) return r.json();
    if (r.status === 429) {
      const ra = r.headers.get("retry-after");
      wait = ra && /^[\d.]+$/.test(ra) ? Math.min(30, Number(ra)) : wait;
      if (i < attempts - 1) { await sleep(wait); wait = Math.min(30, wait * 2); continue; }
      throw new MPError("Matterport API rate limit (429)", 429, wait);
    }
    throw new MPError(`Matterport API HTTP ${r.status}`, r.status);
  }
  throw new MPError("Matterport API unavailable");
}

const errores = (r) => (r.errors || []).slice(0, 3).map((e) => String(e.message).slice(0, 120)).join("; ");

// ---------------------------------------------------------------- room outlines
const TOL_NODO = 0.02;        // m: vertices closer than this (2 cm cell) are the same node when the id is missing
// A spur (loose edge attached to the outline that forms no loop) is normal: tolerated and recorded in polygonSpurEdges.
// With false the validation is strict: any edge not used by a loop makes the polygon unreliable.
const TOLERAR_MUNONES = true;
const RATIO_MIN = 0.8, RATIO_MAX = 1.3; // polygon area / API area (the outline runs along the wall axis: ~5 % larger)
const TOL_T = 0.03;           // m: a loose end closer than this to another edge rests on it (T junction)

/** Signed area (shoelace formula). */
function area(ring) {
  const n = ring.length;
  return 0.5 * pySum(ring.map((p, i) => p[0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * p[1]));
}

/** Number of pairs of non-adjacent sides that cross (proper self-intersections). */
function cruces(ring) {
  const n = ring.length, o = (a, b, p) => (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
  let c = 0;
  for (let i = 0; i < n; i++) {
    const a = ring[i], b = ring[(i + 1) % n];
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue; // adjacent sides (ring closure)
      const p = ring[j], q = ring[(j + 1) % n];
      const d1 = o(a, b, p), d2 = o(a, b, q), d3 = o(p, q, a), d4 = o(p, q, b);
      if (d1 * d2 < -1e-12 && d3 * d4 < -1e-12) c++;
    }
  }
  return c;
}

/** Edges of a BoundedRegion as segments [[id, [x, y]], [id, [x, y]]]. */
function segmentos(region) {
  const segs = [];
  for (const e of (region || {}).edges || []) {
    const vs = e.vertices || [];
    for (let i = 0; i + 1 < vs.length; i++) segs.push([vs[i], vs[i + 1]].map((v) => [v.id ?? null, [v.position.x, v.position.y]]));
  }
  return segs;
}

/** Nodes joined by vertex id and, if missing, by coordinates rounded to 2 cm. Returns [unique edges, positions]. */
function grafo(segs) {
  const padre = new Map(), pos = new Map(), porId = new Map(), porCelda = new Map();
  const raiz = (n) => { while (padre.get(n) !== n) { padre.set(n, padre.get(padre.get(n))); n = padre.get(n); } return n; };
  const unir = (a, b) => { a = raiz(a); b = raiz(b); if (a !== b) padre.set(b, a); };
  const nodo = (vid, p) => {
    const celda = `${_p(p[0] / TOL_NODO)},${_p(p[1] / TOL_NODO)}`;
    let n;
    if (vid !== null && porId.has(vid)) n = porId.get(vid);
    else if (porCelda.has(celda)) n = porCelda.get(celda);
    else { n = padre.size; padre.set(n, n); pos.set(n, p); }
    if (vid !== null) { if (porId.has(vid)) unir(porId.get(vid), n); if (!porId.has(vid)) porId.set(vid, n); }
    if (porCelda.has(celda)) unir(porCelda.get(celda), n);
    if (!porCelda.has(celda)) porCelda.set(celda, n);
    return n;
  };
  const pares = segs.map(([a, b]) => { const na = nodo(a[0], a[1]); return [na, nodo(b[0], b[1])]; });
  // loose ends within 2 cm of another loose end are joined
  const grado = new Map();
  for (let [a, b] of pares) {
    a = raiz(a); b = raiz(b);
    if (a !== b) { grado.set(a, (grado.get(a) || 0) + 1); grado.set(b, (grado.get(b) || 0) + 1); }
  }
  const sueltos = [...grado].filter(([, g]) => g === 1).map(([n]) => n);
  for (let i = 0; i < sueltos.length; i++)
    for (const b of sueltos.slice(i + 1)) if (raiz(sueltos[i]) !== raiz(b) && dist(pos.get(sueltos[i]), pos.get(b)) <= TOL_NODO) unir(sueltos[i], b);
  const aristas = [], vistas = new Set();
  for (let [a, b] of pares) {
    a = raiz(a); b = raiz(b);
    const k = `${Math.min(a, b)},${Math.max(a, b)}`;
    if (a !== b && !vistas.has(k)) { vistas.add(k); aristas.push([a, b]); } // no degenerate loops or repeated edges
  }
  const posiciones = new Map();
  for (const [n, p] of pos) posiciones.set(raiz(n), p);
  return [partirEnT(aristas, posiciones), posiciones];
}

/** T junctions: if a loose end (degree 1) lies on the interior of another edge, that edge is split there. */
function partirEnT(aristas, pos) {
  for (let it = 0; it < aristas.length + 1; it++) {
    const grado = new Map();
    for (const [a, b] of aristas) { grado.set(a, (grado.get(a) || 0) + 1); grado.set(b, (grado.get(b) || 0) + 1); }
    let cambio = false;
    for (const n of [...grado].filter(([, g]) => g === 1).map(([k]) => k)) {
      const [px, py] = pos.get(n);
      for (let i = 0; i < aristas.length; i++) {
        const [a, b] = aristas[i];
        if (n === a || n === b) continue;
        const [ax, ay] = pos.get(a), [bx, by] = pos.get(b);
        const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
        if (L2 < 1e-12) continue;
        const t = ((px - ax) * dx + (py - ay) * dy) / L2;
        if (t > 0 && t < 1 && Math.hypot(px - (ax + t * dx), py - (ay + t * dy)) <= TOL_T) {
          aristas.splice(i, 1, [a, n], [n, b]); // split the edge at the loose end
          cambio = true;
          break;
        }
      }
      if (cambio) break;
    }
    if (!cambio) break;
  }
  return aristas;
}

/** Walk that extracts ALL closed loops (lists of nodes). At a node with more than 2 edges it follows the one that turns
 *  least relative to the current direction. Returns [loops, Set of indices of edges that belong to some loop]. */
function lazos(aristas, pos) {
  const ady = new Map();
  aristas.forEach(([a, b], i) => {
    if (!ady.has(a)) ady.set(a, []); ady.get(a).push(i);
    if (!ady.has(b)) ady.set(b, []); ady.get(b).push(i);
  });
  const otro = (i, n) => (aristas[i][0] === n ? aristas[i][1] : aristas[i][0]);
  const rumbo = (a, b) => Math.atan2(pos.get(b)[1] - pos.get(a)[1], pos.get(b)[0] - pos.get(a)[0]);
  const giro = (d0, d1) => Math.abs(pyMod(d1 - d0 + Math.PI, 2 * Math.PI) - Math.PI);
  const usada = aristas.map(() => false), enLazo = new Set(), out = [];
  const aristaDe = new Map(aristas.map(([a, b], i) => [`${Math.min(a, b)},${Math.max(a, b)}`, i]));
  for (let ini = 0; ini < aristas.length; ini++) {
    if (usada[ini]) continue;
    usada[ini] = true;
    let camino = [aristas[ini][0], aristas[ini][1]];
    const idx = new Map([[camino[0], 0], [camino[1], 1]]);
    while (camino.length >= 2) {
      const actual = camino[camino.length - 1], previo = camino[camino.length - 2];
      const cand = ady.get(actual).filter((j) => !usada[j]);
      if (!cand.length) { idx.delete(camino.pop()); continue; } // dead end (a spur): back up to the last node with free edges
      const d0 = rumbo(previo, actual);
      let j = cand[0], mejor = giro(d0, rumbo(actual, otro(j, actual)));
      for (const k of cand.slice(1)) { const g = giro(d0, rumbo(actual, otro(k, actual))); if (g < mejor) { mejor = g; j = k; } }
      usada[j] = true;
      const sig = otro(j, actual);
      if (idx.has(sig)) { // the path closes on itself: extract the loop and continue from that node
        const k = idx.get(sig), lazo = camino.slice(k);
        if (lazo.length >= 3) {
          out.push(lazo);
          lazo.forEach((a, m) => { const b = lazo[(m + 1) % lazo.length]; enLazo.add(aristaDe.get(`${Math.min(a, b)},${Math.max(a, b)}`)); });
        }
        for (const n of camino.slice(k + 1)) idx.delete(n);
        camino = camino.slice(0, k + 1);
      } else { camino.push(sig); idx.set(sig, camino.length - 1); }
    }
  }
  return [out, enLazo];
}

/** Loops (lists of points) of a region and edge counts: [loops, loose, spurs, total].
 *  loose = edges outside every loop and disconnected from them; spurs = edges outside the loops but attached to one. */
function regionLazos(region) {
  const [aristas, pos] = grafo(segmentos(region));
  const [ls, enLazo] = lazos(aristas, pos);
  const nodosLazo = new Set(ls.flat());
  const resto = aristas.map((_, i) => i).filter((i) => !enLazo.has(i));
  const padre = new Map();
  const raiz = (n) => { if (!padre.has(n)) padre.set(n, n); while (padre.get(n) !== n) { padre.set(n, padre.get(padre.get(n))); n = padre.get(n); if (!padre.has(n)) padre.set(n, n); } return n; };
  for (const i of resto) { const [a, b] = aristas[i]; padre.set(raiz(a), raiz(b)); }
  const unidas = new Set();
  for (const i of resto) for (const n of aristas[i]) if (nodosLazo.has(n)) unidas.add(raiz(n)); // components touching a loop
  const munones = resto.filter((i) => unidas.has(raiz(aristas[i][0]))).length;
  return [ls.map((l) => l.map((n) => pos.get(n))), resto.length - munones, munones, aristas.length];
}

/** Outline = loop with the largest area; other loops and API holes are holes. Validates the result. */
function contorno(boundary, holesApi, areaApi) {
  let [ls, sinUsar, munones, nAristas] = regionLazos(boundary);
  const res = { polygon: null, polygonQuality: "missing", polygonAreaM2: null, polygonAreaRatio: null, polygonIssues: [], polygonSpurEdges: 0, bbox: null };
  if (!nAristas) return res;
  const todos = ((boundary || {}).edges || []).flatMap((e) => (e.vertices || []).map((v) => [v.position.x, v.position.y]));
  const xs = todos.map((p) => p[0]), ys = todos.map((p) => p[1]);
  res.bbox = [_p(Math.min(...xs), 2), _p(Math.min(...ys), 2), _p(Math.max(...xs), 2), _p(Math.max(...ys), 2)];
  let huecos = [];
  for (const h of holesApi || []) { const [l, su, mu] = regionLazos(h); huecos = huecos.concat(l); sinUsar += su; munones += mu; }
  const problemas = [];
  if (!ls.length) { problemas.push("no-closed-loop"); return Object.assign(res, { polygonQuality: "unreliable", polygonIssues: problemas }); }
  ls = ls.map((l, i) => [l, i]).sort((a, b) => -Math.abs(area(a[0])) - -Math.abs(area(b[0])) || a[1] - b[1]).map(([l]) => l); // stable
  const cont = ls[0], interiores = ls.slice(1).concat(huecos);
  const nCruces = cruces(cont) + pySum(interiores.map(cruces));
  const neta = Math.abs(area(cont)) - pySum(interiores.map((h) => Math.abs(area(h))));
  const ratio = areaApi ? neta / areaApi : null;
  if (sinUsar || (munones && !TOLERAR_MUNONES)) problemas.push(`unused-edges:${sinUsar + (TOLERAR_MUNONES ? 0 : munones)}`);
  if (nCruces) problemas.push(`self-intersections:${nCruces}`);
  if (ratio !== null && !(RATIO_MIN <= ratio && ratio <= RATIO_MAX)) problemas.push(`area-ratio:${pyFixed(ratio, 2)}`);
  const ring = (l) => l.map(([x, y]) => [_p(x, 3), _p(y, 3)]);
  Object.assign(res, { polygon: ring(cont), polygonQuality: problemas.length ? "unreliable" : "ok", polygonAreaM2: _p(neta, 2),
    polygonAreaRatio: ratio !== null ? _p(ratio, 3) : null, polygonIssues: problemas, polygonSpurEdges: munones });
  if (interiores.length) res.holes = interiores.map(ring);
  return res;
}

/** Point and angle (degrees) at fraction `frac` (0..1) of the polyline length. */
function puntoEnPolilinea(pts, frac) {
  const largos = pts.slice(0, -1).map((p, i) => dist(p, pts[i + 1]));
  const total = pySum(largos) || 1e-9;
  let d = Math.max(0, Math.min(1, frac)) * total, i = 0;
  while (i < largos.length - 1 && d > largos[i]) { d -= largos[i]; i++; }
  const t = d / (largos[i] || 1e-9), a = pts[i], b = pts[i + 1];
  return [[_p(a[0] + (b[0] - a[0]) * t, 3), _p(a[1] + (b[1] - a[1]) * t, 3)], _p(Math.atan2(b[1] - a[1], b[0] - a[0]) * (180 / Math.PI), 2)];
}

function sala(r) {
  const dim = r.dimensionEstimates || {};
  const cls = (r.classifications || []).map((c, i) => [c, i]).sort((a, b) => -(a[0].confidence || 0) - -(b[0].confidence || 0) || a[1] - b[1]).map(([c]) => c);
  const a = dim.area;
  return {
    id: r.id, label: r.label || "",
    type: cls.length ? cls[0].label : null, // AI-detected type: can be wrong
    typeConfidence: cls.length ? _p(cls[0].confidence || 0, 2) : null,
    areaM2: a !== null && a !== undefined ? _p(a, 2) : null,
    widthM: dim.width ? _p(dim.width, 2) : null,
    lengthM: dim.height ? _p(dim.height, 2) : null,
    ...contorno(r.boundary, r.holes, a),
  };
}

/** Floors from an API response (data of Q_FLOORS). Pure: used by the live path and by the exactness check. */
export function pisosDesdeRespuesta(r) {
  if (r.errors) throw new MPError(`incomplete data from the API (${errores(r)})`); // any GraphQL error = incomplete data
  const m = (r.data || {}).model;
  if (!m) throw new MPError("model not found or no access");
  const pisos = m.floors || [];
  if (!pySum(pisos.map((f) => (f.edges || []).length))) throw new MPError("the API returned no edges for this model");
  const out = [];
  for (const f of pisos.map((f, i) => [f, i]).sort((a, b) => (a[0].sequence || 0) - (b[0].sequence || 0) || a[1] - b[1]).map(([f]) => f)) {
    const walls = [], openings = [];
    for (const e of f.edges || []) {
      const pts = (e.vertices || []).map((v) => [v.position.x, v.position.y]);
      if (pts.length < 2) continue;
      walls.push({ id: e.id, type: e.type || "wall", thickness: e.thickness ? _p(e.thickness, 3) : null,
        centerLineBias: e.centerLineBias ?? null, points: pts.map(([x, y]) => [_p(x, 3), _p(y, 3)]) });
      for (const o of e.openings || []) {
        const [c, ang] = puntoEnPolilinea(pts, o.relativeCenter);
        openings.push({ id: o.id, type: o.type, wallId: e.id, center: c, angleDeg: ang, width: _p(o.width, 2),
          height: o.height !== null && o.height !== undefined ? _p(o.height, 2) : null,
          lowerElevation: o.lowerElevation !== null && o.lowerElevation !== undefined ? _p(o.lowerElevation, 2) : null,
          label: o.label || null, wallThickness: walls[walls.length - 1].thickness });
      }
    }
    out.push({ id: f.id, label: f.label || `Floor ${(f.sequence || 0) + 1}`, sequence: f.sequence || 0,
      walls, openings, rooms: (f.rooms || []).filter(Boolean).map(sala) });
  }
  if (!out.some((f) => f.walls.length)) throw new MPError("the API returned no usable wall geometry");
  return out;
}

function limites(pisos) {
  const xs = [], ys = [];
  for (const f of pisos) {
    for (const w of f.walls) for (const p of w.points) { xs.push(p[0]); ys.push(p[1]); }
    for (const r of f.rooms) {
      if (r.polygonQuality !== "ok") continue; // an unreliable polygon is never used in a calculation
      for (const p of r.polygon || []) { xs.push(p[0]); ys.push(p[1]); }
    }
  }
  return { minX: _p(Math.min(...xs), 2), minY: _p(Math.min(...ys), 2), maxX: _p(Math.max(...xs), 2), maxY: _p(Math.max(...ys), 2) };
}

const NOTES = ["rooms: type/typeConfidence is the top AI classification (can be wrong); widthM and lengthM come from dimensionEstimates width and height (metric); polygon is the room boundary (by wall axis, roughly 5% larger than the API area); polygonQuality \"unreliable\" means the outline failed validation (unused edges, self-intersection or area ratio outside 0.8-1.3) and must be ignored in any polygon calculation, only areaM2 and bbox are trusted; "
  + "wall openings are placed along their wall at relativeCenter"];

/** Plan JSON from an API response. */
export const planDesdeRespuesta = (mid, r) => {
  const pisos = pisosDesdeRespuesta(r);
  return { modelId: mid, source: "api-edges", units: "meters", bounds: limites(pisos), floors: pisos, notes: NOTES };
};

/** Live: query the API (read-only) and build the plan. */
export async function generar(mid, auth) {
  if (!ID_RE.test(mid || "")) throw new MPError("invalid model id", 400);
  try {
    return planDesdeRespuesta(mid, await gql(Q_FLOORS, { id: mid }, auth));
  } catch (e) {
    if (e instanceof MPError && e.status === null) e.status = 502;
    throw e;
  }
}

/** Model dates (read-only query): { created, modified } as ISO strings, or null when the API does not give them. */
export async function fechasModelo(mid, auth) {
  if (!ID_RE.test(mid || "")) throw new MPError("invalid model id", 400);
  const r = await gql("query($id:ID!){ model(id:$id){ created modified } }", { id: mid }, auth);
  if (r.errors) throw new MPError(`incomplete data from the API (${errores(r)})`, 502);
  const m = (r.data || {}).model;
  return m ? { created: m.created ?? null, modified: m.modified ?? null } : null;
}

/** Same query, raw response (for the exactness check script). */
export const consultar = (mid, auth) => gql(Q_FLOORS, { id: mid }, auth);
