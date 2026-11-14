// Geometria y evacuacion sobre el plano generado (web/plans/gen/<modelId>.json). Modulo puro (sin DOM).
// Solo usa lo que hay en el plano: habitaciones (polygon, type, areaM2), puertas (doorway), paredes invisibles y escaleras.
export const FLOOR_H = 2.7   // altura de planta: ESTIMACION (el plano no trae alturas de planta)
export const SALA_MIN = 2    // m2: las habitaciones menores no llevan rotulo (se dibujan igualmente)
export const LADO = 0.3      // m: distancia a cada lado de una apertura para saber que hay a ambos lados
export const MAX_ESCALERA = 4 // m: distancia horizontal maxima entre escaleras de plantas contiguas para enlazarlas

// lowerElevation de las aperturas esta medido desde la base del modelo, no desde el suelo de cada planta.
// El nivel de una planta se deduce del plano: mediana de lowerElevation de sus puertas (una puerta arranca en el suelo).
export function baseElev(f) {
  const s = f.openings.filter((o) => o.type === 'doorway' && o.lowerElevation != null).map((o) => o.lowerElevation).sort((x, y) => x - y)
  return s.length ? s[s.length >> 1] : 0
}
// Antepecho de una apertura relativo al suelo de su planta, acotado a la altura de planta (estimada)
export const antepecho = (o, base) => Math.max(0, Math.min(FLOOR_H, (o.lowerElevation || 0) - base))

export const esEscalera = (r) => r.type === 'Staircase' || r.type === 'Stairs'
export const poligonoValido = (r) => !!r.polygon && (r.polygonQuality || 'ok') === 'ok'
// Solo el tipo Void (huecos entre muros) queda fuera del grafo y del dibujo; las habitaciones pequenas son reales
// (un pasillo de 1,7 m2 es un paso). Las menores de SALA_MIN solo pierden el rotulo.
export const dibujable = (r) => r.type !== 'Void'

export function dentroAnillo(p, ring) {
  let dentro = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j]
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) dentro = !dentro
  }
  return dentro
}
export const dentroSala = (p, r) => dentroAnillo(p, r.polygon) && !(r.holes || []).some((h) => dentroAnillo(p, h))

export function centroide(ring) {
  let a = 0, cx = 0, cy = 0
  for (let i = 0; i < ring.length; i++) {
    const [x0, y0] = ring[i], [x1, y1] = ring[(i + 1) % ring.length], c = x0 * y1 - x1 * y0
    a += c; cx += (x0 + x1) * c; cy += (y0 + y1) * c
  }
  return Math.abs(a) < 1e-9 ? [ring[0][0], ring[0][1]] : [cx / (3 * a), cy / (3 * a)]
}
// Punto dentro de la sala: el centroide si cae dentro; si no, el centro del tramo interior mas largo de una linea horizontal
export function puntoInterior(r) {
  const c = centroide(r.polygon)
  if (dentroSala(c, r)) return c
  let mejor = null
  const ys = r.polygon.map((p) => p[1]), y0 = Math.min(...ys), y1 = Math.max(...ys)
  for (let k = 1; k < 12; k++) {
    const y = y0 + ((y1 - y0) * k) / 12, xs = []
    for (let i = 0; i < r.polygon.length; i++) {
      const [xa, ya] = r.polygon[i], [xb, yb] = r.polygon[(i + 1) % r.polygon.length]
      if ((ya > y) !== (yb > y)) xs.push(xa + ((y - ya) * (xb - xa)) / (yb - ya))
    }
    xs.sort((a, b) => a - b)
    for (let i = 0; i + 1 < xs.length; i += 2) {
      const p = [(xs[i] + xs[i + 1]) / 2, y]
      if (dentroSala(p, r) && (!mejor || xs[i + 1] - xs[i] > mejor.w)) mejor = { p, w: xs[i + 1] - xs[i] }
    }
  }
  return mejor ? mejor.p : c
}
const area = (r) => r.areaM2 || 0

// Contexto de una planta para clasificar puntos: salas del grafo, huecos (Void) y salas dudosas (solo bbox)
function contexto(f) {
  const nodos = f.rooms.filter((r) => dibujable(r) && poligonoValido(r)).sort((a, b) => area(a) - area(b))
  const vacios = f.rooms.filter((r) => !dibujable(r) && poligonoValido(r))
  const dudosas = f.rooms.filter((r) => dibujable(r) && !poligonoValido(r) && r.bbox)
  return { nodos, vacios, dudosas }
}
// Que hay en el punto p: una sala del grafo, un hueco interior, una zona desconocida (sala dudosa) o nada (fuera)
function clasificar(p, ctx) {
  const sala = ctx.nodos.find((r) => dentroSala(p, r))   // la menor que lo contiene
  if (sala) return { tipo: 'sala', sala }
  if (ctx.vacios.some((r) => dentroSala(p, r))) return { tipo: 'vacio' }
  if (ctx.dudosas.some((r) => p[0] >= r.bbox[0] && p[0] <= r.bbox[2] && p[1] >= r.bbox[1] && p[1] <= r.bbox[3])) return { tipo: 'desconocido' }
  return { tipo: 'fuera' }
}
const lados = (c, anguloDeg) => {
  const a = (anguloDeg * Math.PI) / 180, n = [-Math.sin(a), Math.cos(a)]
  return [[c[0] + LADO * n[0], c[1] + LADO * n[1]], [c[0] - LADO * n[0], c[1] - LADO * n[1]]]
}
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1])
// Punto y rumbo (grados) a la mitad de una polilinea
function mitad(pts) {
  const L = pts.slice(1).map((p, i) => dist(pts[i], p)), total = L.reduce((a, b) => a + b, 0)
  let d = total / 2, i = 0
  while (i < L.length - 1 && d > L[i]) { d -= L[i]; i++ }
  const t = L[i] ? d / L[i] : 0, a = pts[i], b = pts[i + 1]
  return { c: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], ang: (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI }
}

/**
 * Grafo de habitaciones y distancia de evacuacion. Conexiones: puertas (doorway) y paredes invisibles cuyos dos lados
 * (a 0,3 m) caen en habitaciones distintas; entre plantas, solo escaleras (salas Staircase). Una puerta es salida exterior
 * si un lado cae en una habitacion de la planta mas baja y el otro en ninguna. Coste en metros entre centros de habitacion
 * y de puerta. Una habitacion sin ruta queda con dist = null (nunca 0).
 */
export function evacuacion(plan) {
  const pisos = plan.floors
  const ctx = pisos.map(contexto)
  const clave = (fi, r) => fi + '|' + r.id
  const nodos = new Map()
  pisos.forEach((f, fi) => ctx[fi].nodos.forEach((r) => nodos.set(clave(fi, r), { key: clave(fi, r), fi, room: r, c: puntoInterior(r) })))
  const aristas = [], salidas = [], puertas = new Map()  // puertas: id de apertura -> { conecta, salida }
  const ady = new Map([...nodos.keys()].map((k) => [k, []]))
  const unir = (a, b, punto, extra) => {
    const A = nodos.get(a), B = nodos.get(b)
    const e = { a, b, punto, coste: dist(A.c, punto) + dist(punto, B.c), ...extra }
    aristas.push(e); ady.get(a).push(e); ady.get(b).push({ ...e, a: b, b: a })
  }
  pisos.forEach((f, fi) => {
    const probar = (id, c, ang, tipo) => {
      const [p1, p2] = lados(c, ang).map((p) => clasificar(p, ctx[fi]))
      if (p1.tipo === 'sala' && p2.tipo === 'sala' && p1.sala !== p2.sala) {
        unir(clave(fi, p1.sala), clave(fi, p2.sala), c, { tipo, id })
        puertas.set(id, { conecta: [p1.sala.id, p2.sala.id], salida: false, fi })
        return
      }
      const una = p1.tipo === 'sala' ? p1 : p2.tipo === 'sala' ? p2 : null, otra = una === p1 ? p2 : p1
      if (una && otra.tipo === 'fuera' && tipo === 'doorway') {
        // solo cuenta como salida en la planta mas baja; en las demas es una puerta a terraza o balcon
        if (fi === 0) {
          salidas.push({ key: clave(fi, una.sala), punto: c, id, coste: dist(nodos.get(clave(fi, una.sala)).c, c) })
          puertas.set(id, { conecta: [una.sala.id], salida: true, fi })
        } else puertas.set(id, { conecta: [una.sala.id], salida: false, exteriorUpper: true, fi })
      }
    }
    f.openings.filter((o) => o.type === 'doorway').forEach((o) => probar(o.id, o.center, o.angleDeg, 'doorway'))
    f.walls.filter((w) => w.type === 'invisible' && w.points.length >= 2).forEach((w) => { const m = mitad(w.points); probar(w.id, m.c, m.ang, 'invisible') })
  })
  // escaleras: cada escalera se enlaza con la mas cercana de la planta contigua (a menos de MAX_ESCALERA m)
  const escaleras = [...nodos.values()].filter((n) => esEscalera(n.room))
  const unidos = new Set()
  for (const s of escaleras) {
    for (const df of [-1, 1]) {
      const cand = escaleras.filter((t) => t.fi === s.fi + df).map((t) => ({ t, d: dist(s.c, t.c) })).filter((x) => x.d <= MAX_ESCALERA).sort((x, y) => x.d - y.d)[0]
      if (cand && !unidos.has([s.key, cand.t.key].sort().join('~'))) {
        unidos.add([s.key, cand.t.key].sort().join('~'))
        const coste = Math.hypot(cand.d, FLOOR_H)  // 3D: horizontal + altura de planta (estimada)
        const e = { a: s.key, b: cand.t.key, punto: s.c, coste, tipo: 'stairs', id: 'stairs:' + s.key + '>' + cand.t.key }
        aristas.push(e); ady.get(s.key).push(e); ady.get(cand.t.key).push({ ...e, a: cand.t.key, b: s.key })
      }
    }
  }
  // Dijkstra multifuente desde las salidas exteriores
  const d = new Map(), pred = new Map(), hecho = new Set()
  salidas.forEach((s) => { if (!d.has(s.key) || s.coste < d.get(s.key)) { d.set(s.key, s.coste); pred.set(s.key, { salida: s }) } })
  while (true) {
    let u = null
    for (const [k, v] of d) if (!hecho.has(k) && (u === null || v < d.get(u))) u = k
    if (u === null) break
    hecho.add(u)
    for (const e of ady.get(u)) {
      const nd = d.get(u) + e.coste
      if (!d.has(e.b) || nd < d.get(e.b) - 1e-9) { d.set(e.b, nd); pred.set(e.b, { de: u, e }) }
    }
  }
  const rutas = new Map()
  for (const [k, n] of nodos) {
    if (!d.has(k)) { rutas.set(k, null); continue }
    const pts = [{ x: n.c[0], y: n.c[1], fi: n.fi }]
    let cur = k, guard = 0
    while (guard++ < 500) {
      const p = pred.get(cur)
      if (p.salida) { pts.push({ x: p.salida.punto[0], y: p.salida.punto[1], fi: nodos.get(cur).fi, salida: p.salida.id }); break }
      const nxt = nodos.get(p.de), e = p.e
      if (e.tipo !== 'stairs') pts.push({ x: e.punto[0], y: e.punto[1], fi: nodos.get(cur).fi, puerta: e.id })
      pts.push({ x: nxt.c[0], y: nxt.c[1], fi: nxt.fi })
      cur = p.de
    }
    rutas.set(k, { dist: d.get(k), puntos: pts })
  }
  const alcanzables = [...rutas.values()].filter(Boolean)
  const hayEscaleras = escaleras.length > 0
  return {
    nodos, aristas, salidas, puertas, rutas, clave,
    maxDist: alcanzables.length ? Math.max(...alcanzables.map((r) => r.dist)) : 0,
    sinDatosEscaleras: pisos.length >= 2 && !pisos.some((f) => f.rooms.some(esEscalera)),
    escalerasEnlazadas: unidos.size, hayEscaleras,
  }
}
export const rutaDe = (ev, fi, room) => ev.rutas.get(ev.clave(fi, room)) || null

// Escala de color de la distancia: verde (cerca) -> amarillo -> rojo (lejos). t en [0, 1]
export function colorDistancia(t) {
  const s = [[34, 197, 94], [234, 179, 8], [239, 68, 68]], x = Math.max(0, Math.min(1, t)) * 2, i = Math.min(1, Math.floor(x)), f = x - i
  return s[i].map((v, k) => Math.round(v + (s[i + 1][k] - v) * f))
}
