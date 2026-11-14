// Escaleras entre plantas a partir del plano generado. Modulo puro (sin DOM ni Three.js): lo usan casa.js (3D y 2D)
// y tools/escaleras.mjs (medicion y datos para el control de calidad). No cambia datos ni evacuacion.
import { esEscalera, poligonoValido, dibujable, dentroSala, centroide } from './evac.js'

export const EJE_MIN = 1.15      // el eje largo esta definido si el largo supera en un 15 % al ancho
export const ANCHO_PELDANO = 0.92 // ancho de los peldanos respecto al ancho de la sala
export const TOL = 0.05           // m: tolerancia para "dentro" (los contornos van por el eje del muro)
export const ESCALA_MIN = 0.7, ESCALA_PASO = 0.05

export function distContorno(p, poly) {
  let m = 1e9
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy
    const t = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2)) : 0
    m = Math.min(m, Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
  }
  return m
}
export function ejeLargo(poly) {
  const n = poly.length, mx = poly.reduce((a, p) => a + p[0], 0) / n, my = poly.reduce((a, p) => a + p[1], 0) / n
  let sxx = 0, syy = 0, sxy = 0
  poly.forEach((p) => { const dx = p[0] - mx, dy = p[1] - my; sxx += dx * dx; syy += dy * dy; sxy += dx * dy })
  const ang = 0.5 * Math.atan2(2 * sxy, sxx - syy), u = [Math.cos(ang), Math.sin(ang)], v = [-u[1], u[0]]
  const s = poly.map((p) => (p[0] - mx) * u[0] + (p[1] - my) * u[1]), t = poly.map((p) => (p[0] - mx) * v[0] + (p[1] - my) * v[1])
  return { m: [mx, my], u, v, ang, s0: Math.min(...s), s1: Math.max(...s), t0: Math.min(...t), t1: Math.max(...t) }
}
const solapan = (a, b) => a.polygon.some((p) => dentroSala(p, b)) || b.polygon.some((p) => dentroSala(p, a)) || dentroSala(centroide(a.polygon), b)
const nombreSala = (r) => r.label || r.type || 'Room'

// Entradas de una sala de escalera: puertas y paredes invisibles sobre su contorno (centro a menos de 0,35 m)
export function entradas(f, room) {
  const pts = f.openings.filter((o) => o.type === 'doorway' && distContorno(o.center, room.polygon) < 0.35).map((o) => o.center)
  f.walls.filter((w) => w.type === 'invisible' && w.points.length >= 2).forEach((w) => {
    const a = w.points[0], b = w.points[w.points.length - 1], m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
    if (distContorno(m, room.polygon) < 0.35) pts.push(m)
  })
  return pts
}
// Sentido de subida: la entrada de la planta inferior marca el pie y la de la superior el final.
export function sentidoEscalera(plan, fi, r, q) {
  const e = ejeLargo(r.polygon), mid = (e.s0 + e.s1) / 2, L = e.s1 - e.s0, W = e.t1 - e.t0
  if (L < EJE_MIN * W) return { e, dir: null, conf: null, motivo: 'no defined long axis (room is about as wide as it is long)' }
  const proy = (p) => (p[0] - e.m[0]) * e.u[0] + (p[1] - e.m[1]) * e.u[1]
  const lado = (lst) => { if (!lst.length) return null; const sm = lst.reduce((a, p) => a + proy(p), 0) / lst.length; return Math.abs(sm - mid) < L * 0.15 ? 0 : Math.sign(sm - mid) }
  const pie = lado(entradas(plan.floors[fi], r)), fin = lado(entradas(plan.floors[fi + 1], q))
  // dir = +1: el pie esta en s0 y sube hacia s1
  if (pie && fin && pie !== fin) return { e, dir: -pie, conf: 'clara', motivo: 'entries at both ends (lower floor = foot, upper floor = top)' }
  if (pie && !fin) return { e, dir: -pie, conf: 'estimada', motivo: 'only the lower-floor entry was found' }
  if (fin && !pie) return { e, dir: fin, conf: 'estimada', motivo: 'only the upper-floor entry was found' }
  const vecinas = plan.floors[fi].rooms.filter((x) => x !== r && dibujable(x) && poligonoValido(x) && !esEscalera(x) &&
    (x.polygon.some((p) => distContorno(p, r.polygon) < 0.4) || r.polygon.some((p) => distContorno(p, x.polygon) < 0.4)))
    .sort((a, b) => (b.areaM2 || 0) - (a.areaM2 || 0))
  if (vecinas.length) {
    const sv = proy(centroide(vecinas[0].polygon))
    return { e, dir: sv < mid ? 1 : -1, conf: 'estimada', motivo: (pie && fin ? 'entries on both floors at the same end' : 'no usable entries') + '; foot placed toward the largest neighbouring room (' + nombreSala(vecinas[0]) + ')' }
  }
  return { e, dir: 1, conf: 'estimada', motivo: 'no entries and no neighbouring room: direction chosen arbitrarily' }
}

// Contorno de la casa en una planta: union de los poligonos de habitaciones validas (sin Void)
const salasCasa = (f) => f.rooms.filter((r) => dibujable(r) && poligonoValido(r))
const dentroTol = (p, r) => dentroSala(p, r) || distContorno(p, r.polygon) <= TOL
export const enSala = (p, r) => dentroTol(p, r)
export const enCasa = (p, f) => salasCasa(f).some((r) => dentroTol(p, r))

// Rectangulo de la escalera (en el marco del eje largo) escalado k alrededor de su centro
export function rectangulo(e, k = 1) {
  const sc = (e.s0 + e.s1) / 2, tc = (e.t0 + e.t1) / 2, hl = ((e.s1 - e.s0) / 2) * k, hw = ((e.t1 - e.t0) * ANCHO_PELDANO / 2) * k
  return { s0: sc - hl, s1: sc + hl, t0: tc - hw, t1: tc + hw, tc, k }
}
const aPlano = (e, s, t) => [e.m[0] + e.u[0] * s + e.v[0] * t, e.m[1] + e.u[1] * s + e.v[1] * t]
// Puntos de control: 4 esquinas + 4 puntos medios de lado; y las 2 esquinas del extremo superior
export function puntosRect(e, R, dir) {
  const S = [R.s0, (R.s0 + R.s1) / 2, R.s1], Tt = [R.t0, R.tc, R.t1]
  const todos = []; S.forEach((s) => Tt.forEach((t) => { if (!(s === S[1] && t === R.tc)) todos.push(aPlano(e, s, t)) }))
  const esquinas = [[R.s0, R.t0], [R.s1, R.t0], [R.s1, R.t1], [R.s0, R.t1]].map(([s, t]) => aPlano(e, s, t))
  const sTop = dir > 0 ? R.s1 : dir < 0 ? R.s0 : null
  const arriba = sTop == null ? [aPlano(e, (R.s0 + R.s1) / 2, R.tc)] : [aPlano(e, sTop, R.t0), aPlano(e, sTop, R.t1)]
  return { todos, esquinas, arriba }
}
// Cuantas esquinas del rectangulo quedan fuera de la sala, de la casa (planta inferior) y del extremo superior en la planta de arriba
export function fuera(plan, E, R) {
  const { esquinas, arriba } = puntosRect(E.e, R, E.dir)
  return {
    sala: esquinas.filter((p) => !enSala(p, E.r)).length,
    casa: esquinas.filter((p) => !enCasa(p, plan.floors[E.fi])).length,
    arriba: arriba.filter((p) => !enCasa(p, plan.floors[E.fi + 1])).length,
  }
}
function cabe(plan, E, R) {
  const { todos, arriba } = puntosRect(E.e, R, E.dir), f = plan.floors[E.fi]
  return todos.every((p) => enSala(p, E.r) && enCasa(p, f)) && arriba.every((p) => enCasa(p, plan.floors[E.fi + 1]))
}
// Ajuste: reduce el rectangulo por pasos del 5 % hasta que quepa (minimo 70 %). estado: ok | no_cabe | dudosa
export function encajar(plan, E) {
  const antes = fuera(plan, E, rectangulo(E.e, 1))
  if ((E.r.polygonQuality || 'ok') !== 'ok' || (E.q.polygonQuality || 'ok') !== 'ok') return { estado: 'dudosa', escala: null, rect: rectangulo(E.e, 1), antes }
  for (let k = 1; k >= ESCALA_MIN - 1e-9; k -= ESCALA_PASO) {
    const R = rectangulo(E.e, k)
    if (cabe(plan, E, R)) return { estado: 'ok', escala: +k.toFixed(2), rect: R, antes, despues: fuera(plan, E, R) }
  }
  return { estado: 'no_cabe', escala: null, rect: rectangulo(E.e, 1), antes }
}

// Pares de escaleras entre plantas consecutivas. soloValidas = true reproduce el emparejamiento anterior
// (solo salas con poligono fiable); false incluye salas con poligono dudoso para marcarlas como "ubicacion no confirmada".
export function emparejar(plan, soloValidas = false) {
  const ok = (r) => esEscalera(r) && r.polygon && r.polygon.length >= 3 && (!soloValidas || poligonoValido(r))
  const out = []
  for (let fi = 0; fi < plan.floors.length - 1; fi++) {
    const arriba = plan.floors[fi + 1].rooms.filter(ok)
    plan.floors[fi].rooms.filter(ok).forEach((r) => {
      const c = centroide(r.polygon)
      const cand = arriba.map((q) => { const cq = centroide(q.polygon); return { q, d: Math.hypot(c[0] - cq[0], c[1] - cq[1]), sol: solapan(r, q) } }).filter((x) => x.d < 3 || x.sol).sort((a, b) => a.d - b.d)[0]
      if (cand) out.push({ fi, r, q: cand.q, ...sentidoEscalera(plan, fi, r, cand.q) })
    })
  }
  return out
}
// Analisis completo: pares con su ajuste y conexion de cada par de plantas consecutivas.
// Un par de plantas esta conectado si tiene al menos una escalera emparejada que cabe (estado ok).
export function analizar(plan) {
  const pares = emparejar(plan, false).map((E) => ({ ...E, ...encajar(plan, E) }))
  const n = plan.floors.length, conectado = []
  for (let fi = 0; fi < n - 1; fi++) conectado.push(pares.some((E) => E.fi === fi && E.estado === 'ok'))
  return { pares, conectado }
}
