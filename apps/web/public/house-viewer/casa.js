// Visor 3D/2D + mapa de calor de evacuacion de una vivienda, con datos en vivo de la API (/api/house-viewer/sample/<id>).
// Llamadas de red solo al mismo origen: /api/house-viewer/sample/<id> (plano, calidad, escaleras) y /api/house-viewer/vexcel.
// Parametros: ?sample=<id> (por defecto el primero de samples/index.json), ?embed=1 (una sola vista, para iframe),
// ?mode=2d|3d (vista en modo embed). API postMessage: ver README.md (mismo origen solamente).
// Solo se usa lo que hay en el plano generado: nada se inventa (la altura de planta es una estimacion marcada como tal).
// Aspecto: theme.js / theme.css (monocromo). Calculos (grafo de evacuacion): evac.js, sin cambios.
// Modo "Calmado" (por defecto) y modo "Detalle" (densidad completa: puntos, rotulos en todas las plantas, aristas completas).
import * as THREE from './vendor/three/build/three.module.js'
import { OrbitControls } from './vendor/three/examples/jsm/controls/OrbitControls.js'
import { mergeGeometries } from './vendor/three/examples/jsm/utils/BufferGeometryUtils.js'
import { EffectComposer } from './vendor/three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from './vendor/three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from './vendor/three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from './vendor/three/examples/jsm/postprocessing/OutputPass.js'
import { FLOOR_H, SALA_MIN, esEscalera, poligonoValido, dibujable, puntoInterior, evacuacion, baseElev, antepecho, dentroSala, centroide } from './evac.js'
import { T, OP, FX, colorDistancia, aplicarCSS, aplicarTema } from './theme.js'
import { analizar, emparejar, rectangulo, distContorno } from './escaleras.js'
import { buildPlanIndex, emptyPlanIndex } from './incident/plan-index.js'
import { planRoutes, floorOf, anchorPoint } from './incident/planner.js'
import { scenariosFor } from './incident/scenarios.js'
import { validate as validarOverlay } from './incident/validate.js'
import { housePeople, houseRoutes } from './incident/house-routes.js'

aplicarCSS()
const $ = (id) => document.getElementById(id)
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const IGNICION = ['Kitchen', 'Laundry', 'Garage'] // zonas de ignicion que se marcan aparte (etiqueta de texto, sin puntuacion)
const TAG_TXT = { sin_escaleras: 'No staircase detected in a multi-floor plan', dormitorio_sin_ventana: 'A bedroom has no window near its outline',
  sin_dormitorios_detectados: 'No bedrooms detected' }
const DESCARGO = 'Automatic estimate, verify on site.'
const FUENTE = 'Sample floor plan generated from the Matterport API (api-edges), normalized; units in meters.'
const tipoKey = (r) => r.type || 'untyped'
const SIN_RUTA = 'no computable route'
// ---------------------------------------------------------------- modo embed y mensajes con la pagina que lo contiene
const Q = new URLSearchParams(location.search)
const EMBED = Q.get('embed') === '1', MODO = Q.get('mode') === '2d' ? '2d' : '3d'
const DEBUG = Q.get('debug') === '1' // selector de escenarios de incidente de prueba (chip DEBUG)
const TACTIL = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches // iPad y otras pantallas tactiles
const HOST = Q.get('host') === '1'   // dentro de la UI de FlameScout: el panel de consideraciones lo muestra la pagina anfitriona
// unidades: pies y pies cuadrados primero, metros entre parentesis
const FT = 3.28084, FT2 = 10.7639
const pies = (m) => `${Math.round(m * FT)} ft`
const piesM = (m) => `${Math.round(m * FT)} ft (${(+m).toFixed(1)} m)`
const areaTxt = (m2) => `${Math.round(m2 * FT2).toLocaleString('en-US')} sq ft (${(+m2).toFixed(1)} m²)`
const fechaCorta = (iso) => (iso ? new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' }) : null)
const SPLIT = EMBED && Q.get('layout') === 'split' // 3D (izquierda, ~2/3) y 2D a la vez; apilados por debajo de 900 px
const SOLO_2D = EMBED && !SPLIT && MODO === '2d'
// layout=house (pantalla de casa del MDT): 3D + plano 2D + Surroundings, sin paneles ni textos; solo el nombre de la sala
// seleccionada o con personas o fuego reportados. Tocar una sala solo dibuja su ruta de salida mas rapida.
const HOUSE = EMBED && Q.get('layout') === 'house'
const etiquetaCasa = (fi, r) => esSel(fi, r.id) || INC.salas.has(fi + ':' + r.id)
const ORIGEN = location.origin // solo se aceptan y envian mensajes al mismo origen
const avisarPadre = (msg) => { if (window.parent !== window) window.parent.postMessage(msg, ORIGEN) }
const estadoEmbed = (t) => { const el = document.getElementById('estado-embed'); if (el) el.textContent = t || '' }
const C = (h) => new THREE.Color(h)

// ---------------------------------------------------------------- estado
const S = {
  id: null, plan: null, ev: null, cal: null, fila: null,
  capas: { walls: true, doors: true, windows: true, slabs: true, stairs: true, points: true, labels: true, route: true, incident: true, sleeping: true, ignition: false },
  color: 'type', foco: 0, f2d: 0, sep: 0, ctx: FX.ctxDefault, niveles: [], alturas: [], nivelEst: [], escaleras: [], ocultarSup: false, fx: !TACTIL, detalle: false, efectosReducidos: false,
  tipos: new Set(), sel: null, hover: null, vb: null, ms: {}, fps: 0, muros: [],
}
let renderer, camara, escena, controles, composer, bloom, raycaster = new THREE.Raycaster(), vistaListo = false
let P = [], refs = null, caja = null, rutaG = null, rotPlanta = [], escG = [], explG = null, explClave = '', tam = 10, anim = null, detalleHecho = false, rotSucio = true

// ---------------------------------------------------------------- utilidades
const salasDe = (f) => f.rooms.filter(dibujable)
const nombreSala = (r) => r.label || r.type || 'Room'
const piso = (fi) => S.plan.floors[fi]
const salaPorId = (fi, id) => piso(fi).rooms.find((r) => r.id === id)
const rutaSala = (fi, r) => S.ev.rutas.get(S.ev.clave(fi, r)) || null
const esDormitorio = (r) => r.type === 'Bedroom'
const esIgnicion = (r) => IGNICION.includes(r.type)
const visibleSala = (r) => S.tipos.has(tipoKey(r))
const centroSala = (r) => (poligonoValido(r) ? puntoInterior(r) : r.bbox ? [(r.bbox[0] + r.bbox[2]) / 2, (r.bbox[1] + r.bbox[3]) / 2] : null)
const esSalida = (fi, o) => { const p = S.ev.puertas.get(o.id); return !!(p && p.salida && p.fi === fi) }
// nivel real de cada planta (a partir de la altura de sus puertas) + separacion de la vista explosionada
const plantaY = (fi) => (S.niveles[fi] || 0) + fi * S.sep
const altura = (fi) => S.alturas[fi] || FLOOR_H // altura de pared: hasta la losa de la planta siguiente; la ultima, 2,7 m (estimacion)
function calcularNiveles(plan) {
  const n = plan.floors.length, b = plan.floors.map((f) => ({ v: baseElev(f), puertas: f.openings.some((o) => o.type === 'doorway' && o.lowerElevation != null) }))
  const niv = [0], est = [false]
  for (let i = 1; i < n; i++) {
    const g = b[i].v - b[i - 1].v, ok = b[i].puertas && b[i - 1].puertas && g >= 1.8 && g <= 6
    niv.push(niv[i - 1] + (ok ? g : FLOOR_H + FX.slabThick)); est.push(!ok)
  }
  const alt = niv.map((y, i) => (i < n - 1 ? Math.max(2.2, Math.min(6, niv[i + 1] - y - FX.slabThick)) : FLOOR_H))
  return { niv, est, alt }
}
const focoIdx = () => (S.foco === 'all' ? S.f2d : S.foco)
const enfocada = (fi) => S.foco === fi
const esHover = (fi, r) => !!(S.hover && S.hover.fi === fi && S.hover.id === r.id)
const esSel = (fi, id) => !!(S.sel && S.sel.fi === fi && S.sel.id === id)
function mulberry(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296 } }
const hashStr = (s) => [...s].reduce((h, c) => (Math.imul(h, 31) + c.charCodeAt(0)) | 0, 7)
const marcaTxt = () => '' // las salas llevan solo su nombre; dormitorios y zonas de ignicion son capas (Sleeping / Ignition areas)

function colorSala(fi, r) {
  if (S.color !== 'evac') return T.slab
  const ruta = rutaSala(fi, r)
  return ruta ? colorDistancia(S.ev.maxDist ? ruta.dist / S.ev.maxDist : 0) : T.noRoute
}
// rotulo: nombre o tipo (sin tamano) + segunda linea: marca de dormitorio/ignicion, o la distancia en el mapa de calor
function textoEtiqueta(fi, r) {
  if (S.color !== 'evac') return [nombreSala(r), marcaTxt(r)]
  const ruta = rutaSala(fi, r)
  return [nombreSala(r), ruta ? '≈ ' + pies(ruta.dist) : SIN_RUTA]
}

// ---------------------------------------------------------------- muros: tramos colineales unidos y exterior/interior
// Cada muro resultante es una recta [A, B] que agrupa paredes colineales consecutivas. Exterior = solo toca una habitacion.
function calcularMuros(f) {
  const segs = []
  f.walls.forEach((w) => { if (w.type !== 'wall') return; for (let i = 0; i < w.points.length - 1; i++) segs.push({ a: w.points[i], b: w.points[i + 1], t: w.thickness || 0.05, wid: w.id }) })
  const key = (p) => Math.round(p[0] / 0.02) + ':' + Math.round(p[1] / 0.02)
  const nodos = new Map()
  segs.forEach((s, i) => { s.i = i; s.ka = key(s.a); s.kb = key(s.b); for (const k of [s.ka, s.kb]) { if (!nodos.has(k)) nodos.set(k, []); nodos.get(k).push(i) } })
  const dir = (s) => { const dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1], L = Math.hypot(dx, dy) || 1; return [dx / L, dy / L] }
  const colineales = (s1, s2) => { const d1 = dir(s1), d2 = dir(s2); return Math.abs(d1[0] * d2[1] - d1[1] * d2[0]) < 0.03 } // < ~1,7 grados
  const unible = (k) => { const l = nodos.get(k); return l.length === 2 && colineales(segs[l[0]], segs[l[1]]) }
  const usado = new Set(), muros = []
  const salas = salasDe(f).filter(poligonoValido)
  for (const s0 of segs) {
    if (usado.has(s0.i)) continue
    usado.add(s0.i)
    const cadena = [s0]
    let extA = s0.ka, extB = s0.kb, pA = s0.a, pB = s0.b
    const crecer = (lado) => {
      for (;;) {
        const k = lado === 'A' ? extA : extB
        if (!unible(k)) return
        const sig = segs[nodos.get(k).find((j) => !cadena.some((c) => c.i === j))]
        if (!sig || usado.has(sig.i)) return
        usado.add(sig.i); cadena.push(sig)
        const otro = sig.ka === k ? [sig.kb, sig.b] : [sig.ka, sig.a]
        if (lado === 'A') { extA = otro[0]; pA = otro[1] } else { extB = otro[0]; pB = otro[1] }
      }
    }
    crecer('A'); crecer('B')
    let ext = 0, tot = 0
    cadena.forEach((s) => {
      const L = Math.hypot(s.b[0] - s.a[0], s.b[1] - s.a[1]), d = dir(s), n = [-d[1], d[0]], m = [(s.a[0] + s.b[0]) / 2, (s.a[1] + s.b[1]) / 2], off = s.t / 2 + 0.18
      const lado = (sg) => salas.some((r) => dentroSala([m[0] + sg * off * n[0], m[1] + sg * off * n[1]], r))
      if (lado(1) !== lado(-1)) ext += L
      tot += L
    })
    muros.push({ a: pA, b: pB, ka: extA, kb: extB, t: Math.max(...cadena.map((c) => c.t)), ext: ext > tot / 2, wids: new Set(cadena.map((c) => c.wid)), n: cadena.length })
  }
  return muros
}

// ---------------------------------------------------------------- 3D: escena y efectos
function fondoRadial() {
  const c = document.createElement('canvas'); c.width = c.height = 512
  const g = c.getContext('2d'), gr = g.createRadialGradient(256, 230, 10, 256, 256, 380)
  gr.addColorStop(0, T.bg1); gr.addColorStop(1, T.bg0)
  g.fillStyle = gr; g.fillRect(0, 0, 512, 512)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  return t
}
function iniciar3D() {
  const cont = $('vista3d')
  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(FX.pixelRatioMax, window.devicePixelRatio || 1))
  cont.appendChild(renderer.domElement)
  escena = new THREE.Scene()
  escena.background = fondoRadial()
  escena.fog = new THREE.FogExp2(C(T.bg0), 0.02)
  camara = new THREE.PerspectiveCamera(40, 1, 0.1, 800)
  controles = new OrbitControls(camara, renderer.domElement)
  controles.enableDamping = true; controles.dampingFactor = 0.08
  controles.addEventListener('change', () => { rotSucio = true })
  composer = new EffectComposer(renderer)
  composer.setPixelRatio(Math.min(FX.pixelRatioMax, window.devicePixelRatio || 1))
  composer.addPass(new RenderPass(escena, camara))
  bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), FX.bloomStrength, FX.bloomRadius, FX.bloomThreshold)
  composer.addPass(bloom)
  composer.addPass(new OutputPass())
  const ajusta = () => {
    const w = cont.clientWidth || 600, h = cont.clientHeight || 400
    renderer.setSize(w, h); composer.setSize(w, h); bloom.setSize(w, h)
    camara.aspect = w / h; camara.updateProjectionMatrix(); rotSucio = true
  }
  new ResizeObserver(ajusta).observe(cont); ajusta()
  let ini = null, pend = null
  renderer.domElement.addEventListener('pointerdown', (e) => { ini = [e.clientX, e.clientY] })
  renderer.domElement.addEventListener('pointerup', (e) => { if (ini && Math.hypot(e.clientX - ini[0], e.clientY - ini[1]) < 4) recoger(e); ini = null })
  renderer.domElement.addEventListener('pointermove', (e) => { pend = e })
  renderer.domElement.addEventListener('pointerleave', () => { pend = null; ponerHover(null) })
  let n = 0, t0 = performance.now(), k = 0
  ;(function bucle() {
    requestAnimationFrame(bucle)
    if (SOLO_2D) return
    controles.update()
    if (pend) { sobrevolar(pend); pend = null }
    paso()
    if (S.fx) composer.render(); else renderer.render(escena, camara)
    n++; const ahora = performance.now()
    if (ahora - t0 >= 1000) { S.fps = Math.round((n * 1000) / (ahora - t0)); n = 0; t0 = ahora; avisoHud() }
    if (++k % 6 === 0) regla()
  })()
}
const m3 = (x, y, h = 0) => new THREE.Vector3(x, h, -y)

function limpiar3D() {
  const tirar = (o) => o.traverse((x) => { if (x.geometry) x.geometry.dispose(); if (x.material) [].concat(x.material).forEach((m) => { if (m.map) m.map.dispose(); m.dispose() }) })
  P.forEach((p) => { escena.remove(p.g); tirar(p.g) })
  ;[caja, rutaG, explG, ...rotPlanta, ...escG.flatMap((e) => [e.grp, e.etq]), ...P.map((F) => F.tag)].forEach((o) => { if (o) { escena.remove(o); tirar(o) } })
  LEG.clear()
  P = []; rotPlanta = []; escG = []; explG = null; explClave = ''; detalleHecho = false; texRayado = null
  rutaG = new THREE.Group(); escena.add(rutaG)
}

// Tramos macizos de una pared (recorte en puertas y ventanas con su ancho, alto y antepecho reales)
function tramosPared(w, aperturas, base, H = FLOOR_H) {
  const t = w.thickness || 0.05, out = [], pts = w.points
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], dx = x1 - x0, dy = y1 - y0, L = Math.hypot(dx, dy)
    if (L < 1e-6) continue
    const ux = dx / L, uy = dy / L, th = Math.atan2(dy, dx), ext = t / 2
    const spans = aperturas.filter((o) => {
      const s = (o.center[0] - x0) * ux + (o.center[1] - y0) * uy, perp = Math.abs(-(o.center[0] - x0) * uy + (o.center[1] - y0) * ux)
      o._s = s
      return perp < 0.06 && s > -0.01 && s < L + 0.01
    }).map((o) => ({ a: Math.max(0, o._s - o.width / 2), b: Math.min(L, o._s + o.width / 2), lo: antepecho(o, base), top: Math.min(H, antepecho(o, base) + (o.height ?? H)) }))
      .sort((p, q) => p.a - q.a)
    const tramo = (u0, u1, h0, h1) => { if (u1 - u0 >= 0.005 && h1 - h0 >= 0.005) out.push({ x0, y0, ux, uy, th, t, u0, u1, h0, h1, wid: w.id }) }
    let cur = -ext
    for (const s of spans) {
      if (s.a > cur) tramo(cur, s.a, 0, H)
      if (s.lo > 0.02) tramo(s.a, s.b, 0, s.lo)
      if (s.top < H - 0.02) tramo(s.a, s.b, s.top, H)
      cur = Math.max(cur, s.b)
    }
    if (cur < L + ext) tramo(cur, L + ext, 0, H)
  }
  return out
}
// plano vertical (sin grosor) de un tramo, en el eje de la pared
function planoTramo(r) {
  const a = m3(r.x0 + r.ux * r.u0, r.y0 + r.uy * r.u0), b = m3(r.x0 + r.ux * r.u1, r.y0 + r.uy * r.u1)
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute([a.x, r.h0, a.z, b.x, r.h0, b.z, b.x, r.h1, b.z, a.x, r.h1, a.z], 3)); g.setIndex([0, 1, 2, 0, 2, 3])
  return g
}
function cajaTramo(r) {
  const g = new THREE.BoxGeometry(r.u1 - r.u0, r.h1 - r.h0, r.t), mid = (r.u0 + r.u1) / 2
  g.applyMatrix4(new THREE.Matrix4().compose(m3(r.x0 + r.ux * mid, r.y0 + r.uy * mid, (r.h0 + r.h1) / 2), new THREE.Quaternion().setFromEuler(new THREE.Euler(0, r.th, 0)), new THREE.Vector3(1, 1, 1)))
  return g
}
const lineas = (pts, color, base) => new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: C(color), transparent: true, opacity: base, depthWrite: false }))
const matPlano = (color, base, extra = {}) => new THREE.MeshBasicMaterial({ color: C(color), transparent: true, opacity: base, depthWrite: false, side: THREE.DoubleSide, ...extra })

let texRayado = null
function rayado() { // rayado diagonal para escaleras (las UV de ShapeGeometry estan en metros del plano)
  if (texRayado) return texRayado
  const c = document.createElement('canvas'); c.width = c.height = 64
  const g = c.getContext('2d'); g.strokeStyle = '#fff'; g.lineWidth = 7
  for (let i = -64; i <= 128; i += 32) { g.beginPath(); g.moveTo(i, 64); g.lineTo(i + 64, 0); g.stroke() }
  texRayado = new THREE.CanvasTexture(c); texRayado.wrapS = texRayado.wrapT = THREE.RepeatWrapping; texRayado.repeat.set(1 / (FX.hatch * 2), 1 / (FX.hatch * 2))
  return texRayado
}

function texturaRotulo(lineasTxt, grande) {
  const c = document.createElement('canvas'), g = c.getContext('2d'), px = grande ? 44 : 36
  const fuente = (sz, peso) => `${peso} ${sz}px ${T.font}`
  const txt = [lineasTxt[0].toUpperCase(), (lineasTxt[1] || '').toUpperCase()]
  g.font = fuente(px, 500); g.letterSpacing = '4px'
  const w0 = g.measureText(txt[0]).width; g.font = fuente(px * 0.72, 400); const w1 = txt[1] ? g.measureText(txt[1]).width : 0
  c.width = Math.ceil(Math.max(w0, w1) + 24); c.height = txt[1] ? 92 : 56
  g.textAlign = 'center'; g.textBaseline = 'middle'; g.letterSpacing = '4px'
  g.font = fuente(px, 500); g.fillStyle = T.text; g.fillText(txt[0], c.width / 2, 28)
  if (txt[1]) { g.font = fuente(px * 0.72, 400); g.fillStyle = txt[1] === 'SIN RUTA CALCULABLE' ? T.noRoute : T.textDim; g.fillText(txt[1], c.width / 2, 70) }
  // mipmaps: el rotulo se ve mas pequeno que el canvas y sin ellos las letras salen granuladas
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.minFilter = THREE.LinearMipmapLinearFilter; t.generateMipmaps = true
  return { t, aspecto: c.width / c.height, alto: c.height / 56 }
}
// Rotulos legibles: tamano real en metros al acercarse, pero nunca menos de `px` pixeles de alto en pantalla.
// PX_LINEA = alto de una linea de rotulo de habitacion (unos 14 px de letra).
const PX_LINEA = 22, LEG = new Map() // sprite -> { bx, by, px } (aparte de userData, que otras partes reasignan)
function legible(sp, bx, by, px) { LEG.set(sp, { bx, by, px }); sp.scale.set(bx, by, 1) }
function ajustarLegibles() {
  const h = renderer.domElement.clientHeight || 1, k = 2 * Math.tan((camara.fov * Math.PI) / 360)
  for (const [sp, { bx, by, px }] of LEG) {
    if (!sp.parent) { LEG.delete(sp); continue }
    sp.getWorldPosition(_w)
    const pxPorM = h / (k * Math.max(0.01, camara.position.distanceTo(_w))), f = Math.max(1, px / (by * pxPorM))
    sp.scale.set(bx * f, by * f, 1)
  }
}

// ---------------------------------------------------------------- construccion
function construir3D() {
  limpiar3D()
  const plan = S.plan
  refs = { pisos: [], slabs: new Map(), paneles: new Map(), etiquetas: new Map(), paredes: [], get rutaG() { return rutaG } }
  S.muros = plan.floors.map(calcularMuros)
  const b = limitesPlan()
  tam = Math.max(b.maxX - b.minX, b.maxY - b.minY, alturaTotal())
  plan.floors.forEach((f, fi) => {
    const g = new THREE.Group(); escena.add(g)
    const F = { g, fi, ent: [], labels: [], slabs: [], doors: [], picks: [], tramos: [], validas: [], cur: { y: plantaY(fi), v: { outer: 1, inner: 1, outerFill: 1, innerFill: 1, slab: 1, base: 1, focus: 1, points: 1 } }, tgt: null, from: null, puntos: 0, lineasLimpias: 0 }
    // ent: { obj, m, kind: 'fill' | 'line' | 'focus', modo: 'calm' | 'detail' | undefined, capa, base, filtro, fx }
    const reg = (obj, kind, capa, base, extra = {}) => { F.ent.push({ obj, m: obj.material, kind, capa, base, ...extra }); g.add(obj); return obj }
    const porPared = new Map()
    f.openings.forEach((o) => { if (!porPared.has(o.wallId)) porPared.set(o.wallId, []); porPared.get(o.wallId).push(o) })
    const base = baseElev(f), muros = S.muros[fi], H = altura(fi)
    const extDe = new Map(); muros.forEach((m) => m.wids.forEach((w) => extDe.set(w, m.ext)))
    // planos translucidos: exteriores mas opacos que interiores
    const tramos = f.walls.filter((w) => w.type === 'wall').flatMap((w) => tramosPared(w, (porPared.get(w.id) || []).filter((o) => o.type === 'doorway' || o.type === 'window'), base, H))
    F.tramos = tramos
    for (const ext of [true, false]) {
      const geos = tramos.filter((r) => !!extDe.get(r.wid) === ext).map(planoTramo)
      if (!geos.length) continue
      const geo = mergeGeometries(geos); geos.forEach((x) => x.dispose())
      refs.paredes.push(reg(new THREE.Mesh(geo, matPlano(ext ? T.outer : T.inner, ext ? OP.wallOuterFill : OP.wallInnerFill)), ext ? 'outerFill' : 'innerFill', 'walls', ext ? OP.wallOuterFill : OP.wallInnerFill))
    }
    // lineas limpias: arriba y abajo de cada muro unido; verticales solo en extremos libres y esquinas, una por nodo
    const extNodo = new Set(muros.filter((m) => m.ext).flatMap((m) => [m.ka, m.kb]))
    for (const ext of [true, false]) {
      const pts = [], vert = new Map()
      muros.filter((m) => m.ext === ext).forEach((m) => {
        pts.push(m3(m.a[0], m.a[1], 0), m3(m.b[0], m.b[1], 0), m3(m.a[0], m.a[1], H), m3(m.b[0], m.b[1], H))
        vert.set(m.ka, m.a); vert.set(m.kb, m.b)
      })
      vert.forEach((p, k) => { if (ext || !extNodo.has(k)) pts.push(m3(p[0], p[1], 0), m3(p[0], p[1], H)) })
      F.lineasLimpias += pts.length / 2
      if (pts.length) reg(lineas(pts, ext ? T.outer : T.inner, ext ? OP.wallOuterLine : OP.wallInnerLine), ext ? 'outer' : 'inner', 'walls', ext ? OP.wallOuterLine : OP.wallInnerLine, { modo: 'calm' })
    }
    // puertas: contorno fino brillante + arco sutil en el suelo; ventanas: discontinua en antepecho y dintel. Sin relleno.
    f.openings.filter((o) => o.type === 'doorway' || o.type === 'window').forEach((o) => {
      const lo = Math.min(antepecho(o, base), H), top = Math.min(H, lo + (o.height ?? H)), h = Math.max(0.05, top - lo), w = o.width
      const esV = o.type === 'window', capa = esV ? 'windows' : 'doors'
      const grupo = new THREE.Group(); grupo.position.copy(m3(o.center[0], o.center[1], 0)); grupo.rotation.y = (o.angleDeg * Math.PI) / 180; g.add(grupo)
      const V = (x, y, z = 0) => new THREE.Vector3(x, y, z)
      let trazo
      if (esV) {
        trazo = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints([V(-w / 2, lo), V(w / 2, lo), V(-w / 2, top), V(w / 2, top)]),
          new THREE.LineDashedMaterial({ color: C(T.window), dashSize: 0.12, gapSize: 0.08, transparent: true, opacity: OP.window, depthWrite: false }))
        trazo.computeLineDistances()
      } else {
        trazo = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints([V(-w / 2, lo), V(w / 2, lo), V(w / 2, top), V(-w / 2, top)]), new THREE.LineBasicMaterial({ color: C(T.door), transparent: true, opacity: OP.door, depthWrite: false }))
      }
      grupo.add(trazo); F.ent.push({ obj: trazo, m: trazo.material, kind: 'focus', capa, base: esV ? OP.window : OP.door })
      if (!esV && w <= 1.3) {
        const arc = []; for (let i = 0; i <= 16; i++) { const a = (Math.PI / 2) * (i / 16); arc.push(V(-w / 2 + w * Math.cos(a), 0.02, -w * Math.sin(a))) }
        const arco = new THREE.Line(new THREE.BufferGeometry().setFromPoints(arc), new THREE.LineBasicMaterial({ color: C(T.door), transparent: true, opacity: OP.doorArc, depthWrite: false }))
        grupo.add(arco); F.ent.push({ obj: arco, m: arco.material, kind: 'focus', capa, base: OP.doorArc })
      }
      const pick = new THREE.Mesh(new THREE.BoxGeometry(w, h, (o.wallThickness || 0.05) + 0.1), new THREE.MeshBasicMaterial({ visible: false })) // objetivo de seleccion invisible
      pick.position.y = lo + h / 2; pick.userData = { entidad: { kind: esV ? 'window' : 'door', id: o.id, fi }, capa }
      grupo.add(pick)
      F.doors.push({ o, trazo, col: esV ? T.window : T.door }); F.picks.push(pick); refs.paneles.set(o.id, pick)
    })
    // losas: mismo tinte para todas; escaleras con rayado diagonal; contorno dudoso: sin losa, solo rotulo
    salasDe(f).forEach((r) => {
      const filtro = () => visibleSala(r)
      if (poligonoValido(r)) {
        F.validas.push(r)
        const forma = new THREE.Shape(r.polygon.map((p) => new THREE.Vector2(p[0], p[1])))
        ;(r.holes || []).forEach((hh) => forma.holes.push(new THREE.Path(hh.map((p) => new THREE.Vector2(p[0], p[1])))))
        const geo = new THREE.ShapeGeometry(forma); geo.rotateX(-Math.PI / 2); geo.translate(0, 0.01, 0)
        const losa = new THREE.Mesh(geo, matPlano(T.slab, OP.slab))
        losa.userData = { entidad: { kind: 'room', id: r.id, fi } }
        F.ent.push({ obj: losa, m: losa.material, kind: 'slab', capa: 'slabs', base: OP.slab, filtro }); g.add(losa)
        let rayas = null
        if (esEscalera(r)) {
          rayas = new THREE.Mesh(geo.clone(), matPlano(T.stairs, OP.stairs, { alphaMap: rayado() }))
          rayas.position.y = 0.005
          F.ent.push({ obj: rayas, m: rayas.material, kind: 'slab', capa: 'slabs', base: OP.stairs, filtro }); g.add(rayas)
        }
        F.slabs.push({ r, losa, rayas }); refs.slabs.set(S.ev.clave(fi, r), losa)
      }
      if ((r.areaM2 || 0) >= SALA_MIN || esEscalera(r)) {
        const c = centroSala(r)
        if (c) { // rotulo a la altura de la losa
          const sp = new THREE.Sprite(new THREE.SpriteMaterial({ depthTest: false, depthWrite: false, transparent: true, opacity: 0, fog: false }))
          sp.position.copy(m3(c[0], c[1], 0.05)); sp.renderOrder = 10; sp.userData = { room: r, fi, area: r.areaM2 || 0 }
          g.add(sp); F.labels.push(sp); refs.etiquetas.set(S.ev.clave(fi, r), sp)
        }
      }
    })
    // puntos del modo Calmado: ~800 por planta, solo sobre el suelo, poca luminosidad; detras del interruptor Efectos
    const nube = nubeSuelo(F.validas, fi, FX.calmPoints, T.inner, OP.points)
    F.puntos = nube.geometry.attributes.position.count
    F.ent.push({ obj: nube, m: nube.material, kind: 'points', capa: 'points', base: OP.points, modo: 'calm', fx: true }); g.add(nube)
    // losa con grosor bajo la planta: el apilado se lee como una sola estructura
    if (F.validas.length) {
      const geos = F.validas.map((r) => { const gg = new THREE.ExtrudeGeometry(new THREE.Shape(r.polygon.map((p) => new THREE.Vector2(p[0], p[1]))), { depth: FX.slabThick, bevelEnabled: false }); gg.rotateX(-Math.PI / 2); gg.translate(0, -FX.slabThick, 0); return gg })
      const losaBase = new THREE.Mesh(mergeGeometries(geos), matPlano(T.inner, OP.baseSlab, { side: THREE.FrontSide })); geos.forEach((x) => x.dispose())
      F.ent.push({ obj: losaBase, m: losaBase.material, kind: 'base', capa: 'slabs', base: OP.baseSlab }); g.add(losaBase)
    }
    // etiqueta "P1", "P2"... en la esquina de la planta cuando es fantasma
    const bp = limitesPiso(f), { t: tt, aspecto: asp } = texturaRotulo(['P' + (fi + 1), ''], true)
    const tag = new THREE.Sprite(new THREE.SpriteMaterial({ map: tt, depthTest: false, transparent: true, opacity: 0, fog: false }))
    const ht = Math.max(0.35, tam * 0.025); legible(tag, ht * asp, ht, PX_LINEA); tag.center.set(1, 0); tag.renderOrder = 12
    tag.userData = { x: bp.minX, z: -bp.maxY }; escena.add(tag); F.tag = tag
    P.push(F); refs.pisos.push(g)
  })
  construirEscaleras()
  caja = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), new THREE.LineBasicMaterial({ color: C(T.inner), transparent: true, opacity: OP.bbox, depthWrite: false, fog: false }))
  escena.add(caja)
  // "FLOOR 1", "FLOOR 2"... a un lado, solo en la vista ALL
  plan.floors.forEach((f, fi) => {
    const { t, aspecto } = texturaRotulo(['FLOOR ' + (fi + 1), ''], true)
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true, opacity: 0, fog: false }))
    const h = Math.max(0.45, tam * 0.035); legible(sp, h * aspecto, h, PX_LINEA * 1.1); sp.center.set(0, 0.5)
    sp.userData = { fi, x: b.maxX + 0.8, z: -(b.minY + b.maxY) / 2 }; sp.renderOrder = 11
    escena.add(sp); rotPlanta.push(sp)
  })
  escena.fog.density = FX.fog / tam
  encuadre()
  if (S.detalle) construirDetalle()
  objetivos(true)
  estilo3D()
  dibujarIncidente3D()
}
const alturaTotal = () => plantaY(S.plan.floors.length - 1) + altura(S.plan.floors.length - 1)

// ---------------------------------------------------------------- escaleras 3D
// Pareja: sala Staircase en la planta fi y otra en fi+1 con el centroide a menos de 3 m o con los poligonos solapados.
// Sentido: la puerta de la sala inferior marca el pie y la de la superior el final (proyectadas sobre el eje largo).
function construirEscaleras() {
  // pares, sentido y ajuste de la huella (web/escaleras.js). Diagnostico: ?huella=original dibuja como antes del ajuste
  S.escaleras = new URLSearchParams(location.search).get('huella') === 'original'
    ? emparejar(S.plan, true).map((E) => ({ ...E, estado: 'ok', escala: 1, rect: rectangulo(E.e, 1) }))
    : analizar(S.plan).pares
  S.escaleras.forEach((E) => {
    const H = (S.niveles[E.fi + 1] - S.niveles[E.fi]) || FLOOR_H, e = E.e, R = E.rect, L = R.s1 - R.s0, W = R.t1 - R.t0
    const grp = new THREE.Group(); escena.add(grp)
    const mats = []
    const punto = (s, t) => [e.m[0] + e.u[0] * s + e.v[0] * t, e.m[1] + e.u[1] * s + e.v[1] * t]
    const caja3 = (s, w, h, ancho, y0) => { const g = new THREE.BoxGeometry(w, h, ancho); const p = punto(s, R.tc); g.applyMatrix4(new THREE.Matrix4().compose(m3(p[0], p[1], y0 + h / 2), new THREE.Quaternion().setFromEuler(new THREE.Euler(0, e.ang, 0)), new THREE.Vector3(1, 1, 1))); return g }
    const ubicada = E.estado === 'ok'
    const discontinua = !ubicada || E.conf !== 'clara'
    const matL = (base) => { const m = discontinua ? new THREE.LineDashedMaterial({ color: C(T.stairs), dashSize: 0.14, gapSize: 0.09, transparent: true, opacity: base, depthWrite: false }) : new THREE.LineBasicMaterial({ color: C(T.stairs), transparent: true, opacity: base, depthWrite: false }); mats.push({ m, base }); return m }
    let geo = null
    if (ubicada && E.dir) { // peldanos dentro del rectangulo ajustado (cabe en la sala y en la casa)
      const n = Math.max(3, Math.round(H / FX.riser)), riser = H / n, run = L / n
      const geos = []
      for (let i = 0; i < n; i++) geos.push(caja3(E.dir > 0 ? R.s0 + (i + 0.5) * run : R.s1 - (i + 0.5) * run, run, riser, W, i * riser))
      geo = mergeGeometries(geos); geos.forEach((x) => x.dispose())
      E.peldanos = n; E.contrahuella = riser
    } else if (ubicada) { // sin eje largo definido: bloque rayado
      geo = caja3((R.s0 + R.s1) / 2, L, H, W, 0)
      const tex = rayado().clone(); tex.needsUpdate = true; tex.repeat.set(Math.max(2, L / (FX.hatch * 2)), Math.max(2, H / (FX.hatch * 2)))
      const bloque = new THREE.Mesh(geo.clone(), new THREE.MeshBasicMaterial({ color: C(T.stairs), alphaMap: tex, transparent: true, opacity: OP.stairs, depthWrite: false, side: THREE.DoubleSide }))
      mats.push({ m: bloque.material, base: OP.stairs }); grp.add(bloque)
    }
    if (geo) {
      const relleno = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: C(T.stairs), transparent: true, opacity: OP.stairsFill, depthWrite: false }))
      mats.push({ m: relleno.material, base: OP.stairsFill }); grp.add(relleno)
      const aristas = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 25), matL(OP.stairsLine)); if (discontinua) aristas.computeLineDistances(); grp.add(aristas)
    }
    // huella en el suelo: el rectangulo ajustado si cabe; si no, el contorno de la sala (discontinuo)
    const huellaPts = ubicada ? [[R.s0, R.t0], [R.s1, R.t0], [R.s1, R.t1], [R.s0, R.t1]].map(([s, t]) => punto(s, t)) : E.r.polygon
    const huella = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(huellaPts.map((p) => m3(p[0], p[1], 0.03))), matL(OP.stairsLine)); if (discontinua) huella.computeLineDistances(); grp.add(huella)
    let etq = null
    const texto = !ubicada ? 'stairs: location not confirmed' : !E.dir ? 'direction not confirmed' : E.conf !== 'clara' ? 'estimated direction' : null
    if (texto) {
      const { t, aspecto } = texturaRotulo([texto, ''])
      etq = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true, opacity: 0.9, fog: false }))
      const h = 0.22; escena.add(etq); legible(etq, h * aspecto, h, PX_LINEA * 0.9); etq.renderOrder = 13
    }
    escG.push({ grp, etq, mats, H, E, cx: ubicada ? punto((R.s0 + R.s1) / 2, R.tc) : e.m, soloHuella: !ubicada })
  })
}
const mid = (e) => (e.s0 + e.s1) / 2
// lineas verticales discontinuas en las esquinas del contorno exterior, de cada planta a la siguiente (vista explosionada)
function lineasExplosion() {
  const clave = S.sep > 0.01 ? P.map((F) => F.cur.y.toFixed(3)).join(',') : ''
  if (clave === explClave) return
  explClave = clave
  if (explG) { escena.remove(explG); explG.geometry.dispose(); explG.material.dispose(); explG = null }
  if (!clave) return
  const pts = []
  for (let fi = 0; fi < P.length - 1; fi++) {
    const nodos = new Map(); S.muros[fi].filter((m) => m.ext).forEach((m) => { nodos.set(m.ka, m.a); nodos.set(m.kb, m.b) })
    const y0 = P[fi].cur.y + altura(fi), y1 = P[fi + 1].cur.y - FX.slabThick
    if (y1 - y0 > 0.05) nodos.forEach((p) => pts.push(m3(p[0], p[1], y0), m3(p[0], p[1], y1)))
  }
  if (!pts.length) return
  explG = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: C(T.inner), dashSize: 0.12, gapSize: 0.1, transparent: true, opacity: OP.explode, depthWrite: false }))
  explG.computeLineDistances(); escena.add(explG)
}
const primeraPlanta = (plan) => Math.max(0, plan.floors.findIndex((f) => salasDe(f).some(poligonoValido)))

// camara a ~50 grados sobre el horizonte (vista mas cenital)
function encuadre() {
  const b = limitesPlan(), cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2
  const ty = S.foco === 'all' ? alturaTotal() / 2.4 : plantaY(S.foco) + 0.4
  const pol = (FX.camPolarDeg * Math.PI) / 180, az = (-30 * Math.PI) / 180
  const d = Math.max(b.maxX - b.minX, b.maxY - b.minY, 6) * 2.15 + (S.foco === 'all' ? alturaTotal() * 1.5 : 0)
  controles.target.set(cx, ty, -cy)
  camara.position.set(cx + d * Math.sin(pol) * Math.sin(az), ty + d * Math.cos(pol), -cy + d * Math.sin(pol) * Math.cos(az))
  camara.near = 0.1; camara.far = tam * 30; camara.updateProjectionMatrix(); controles.update()
}

function nubeSuelo(salas, fi, n, color, op, aditiva) {
  const rnd = mulberry(hashStr(S.id + ':suelo:' + fi + ':' + n)), pos = []
  const areas = salas.map((r) => r.areaM2 || 0), sum = areas.reduce((a, b) => a + b, 0) || 1
  salas.forEach((r, i) => {
    let k = Math.round((n * areas[i]) / sum), intentos = k * 8
    const xs = r.polygon.map((p) => p[0]), ys = r.polygon.map((p) => p[1]), x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys)
    while (k > 0 && intentos-- > 0) { const p = [x0 + rnd() * (x1 - x0), y0 + rnd() * (y1 - y0)]; if (dentroSala(p, r)) { pos.push(p[0], 0.03, -p[1]); k-- } }
  })
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  return new THREE.Points(geo, new THREE.PointsMaterial({ color: C(color), size: FX.pointSize, sizeAttenuation: true, transparent: true, opacity: op, depthWrite: false, blending: aditiva ? THREE.AdditiveBlending : THREE.NormalBlending }))
}

// Modo Detalle: densidad completa (aristas de todas las cajas, nube en paredes y suelos). Se construye la primera vez que se pide.
function construirDetalle() {
  if (detalleHecho) return
  detalleHecho = true
  P.forEach((F) => {
    if (F.tramos.length) {
      const geos = F.tramos.map(cajaTramo), geo = mergeGeometries(geos); geos.forEach((x) => x.dispose())
      const aristas = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 25), new THREE.LineBasicMaterial({ color: C(T.inner), transparent: true, opacity: OP.detailEdge, depthWrite: false }))
      geo.dispose()
      F.ent.push({ obj: aristas, m: aristas.material, kind: 'outer', capa: 'walls', base: OP.detailEdge, modo: 'detail' }); F.g.add(aristas)
    }
    const rnd = mulberry(hashStr(S.id + ':det:' + F.fi)), pos = []
    const aW = F.tramos.map((r) => (r.u1 - r.u0) * (r.h1 - r.h0) * 2), aS = F.validas.map((r) => r.areaM2 || 0)
    const total = aW.reduce((a, b) => a + b, 0) + aS.reduce((a, b) => a + b, 0)
    const n = Math.round(Math.max(FX.detailMin, Math.min(FX.detailMax, total * FX.detailPerM2))), nW = Math.round(n * 0.62), sW = aW.reduce((a, b) => a + b, 0) || 1
    F.tramos.forEach((r, i) => { const k = Math.round((nW * aW[i]) / sW); for (let j = 0; j < k; j++) { const u = r.u0 + rnd() * (r.u1 - r.u0), h = r.h0 + rnd() * (r.h1 - r.h0), l = (rnd() < 0.5 ? -1 : 1) * r.t / 2; pos.push(r.x0 + r.ux * u - r.uy * l, h, -(r.y0 + r.uy * u + r.ux * l)) } })
    const suelo = nubeSuelo(F.validas, F.fi, n - nW, T.outer, 1); suelo.geometry.attributes.position.array.forEach((v) => pos.push(v)); suelo.geometry.dispose(); suelo.material.dispose()
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    const nube = new THREE.Points(geo, new THREE.PointsMaterial({ color: C(T.outer), size: FX.pointSize, sizeAttenuation: true, transparent: true, opacity: OP.detailPoints, depthWrite: false, blending: THREE.AdditiveBlending }))
    F.ent.push({ obj: nube, m: nube.material, kind: 'points', capa: 'points', base: OP.detailPoints, modo: 'detail' }); F.g.add(nube)
    F.puntosDetalle = pos.length / 3
  })
  aplicarPlantas()
}

// ---------------------------------------------------------------- plantas: foco, fantasmas, separacion y transiciones
// Planta enfocada (o todas): completa. Por debajo: contorno exterior marcado, interiores muy tenues, losa casi invisible
// (todo escalado por el control Context). Por encima: solo el contorno exterior (y las escaleras, aparte).
function objetivoPlanta(fi) {
  const y = plantaY(fi), c = S.ctx
  if (S.foco === 'all' || S.foco === fi) return { y, v: { outer: 1, inner: 1, outerFill: 1, innerFill: 1, slab: 1, base: 1, focus: S.foco === fi ? 1 : 0, points: 1 } }
  if (fi < S.foco) return { y, v: { outer: c, inner: c * 0.2, outerFill: c * 0.3, innerFill: c * 0.06, slab: c * 0.12, base: c * 0.5, focus: 0, points: 0 } }
  return { y, v: { outer: S.ocultarSup ? 0 : c * 0.55, inner: 0, outerFill: 0, innerFill: 0, slab: 0, base: 0, focus: 0, points: 0 } }
}
function objetivos(inmediato = false) {
  const camDe = controles.target.y, camA = S.foco === 'all' ? alturaTotal() / 2.4 : plantaY(S.foco) + 0.4
  P.forEach((F) => { F.from = { y: F.cur.y, v: { ...F.cur.v } }; F.tgt = objetivoPlanta(F.fi) })
  rotSucio = true
  if (inmediato) { P.forEach((F) => { F.cur = { y: F.tgt.y, v: { ...F.tgt.v } } }); aplicarPlantas(); anim = null; return }
  anim = { t0: performance.now(), camDe, camA }
}
const suave = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
function paso() {
  if (anim) {
    const t = Math.min(1, (performance.now() - anim.t0) / FX.transitionMs), e = suave(t)
    P.forEach((F) => { F.cur.y = F.from.y + (F.tgt.y - F.from.y) * e; for (const k in F.tgt.v) F.cur.v[k] = F.from.v[k] + (F.tgt.v[k] - F.from.v[k]) * e })
    const dy = anim.camDe + (anim.camA - anim.camDe) * e - controles.target.y
    controles.target.y += dy; camara.position.y += dy
    aplicarPlantas(); rotSucio = true
    if (t >= 1) anim = null
  }
  if (P.length && rotSucio) { ajustarLegibles(); rotulos(); rotSucio = false }
  animarIncidente()
}
function aplicarPlantas() {
  P.forEach((F) => {
    F.g.position.y = F.cur.y
    for (const e of F.ent) {
      let on = S.capas[e.capa] !== false && (!e.filtro || e.filtro())
      if (e.modo === 'calm' && S.detalle) on = false   // en Detalle: nube densa y aristas completas sustituyen a las de Calmado
      if (e.modo === 'detail' && !S.detalle) on = false
      if (e.fx && !S.fx) on = false
      const k = e.kind === 'focus' ? (S.detalle ? F.cur.v.slab : F.cur.v.focus) : (F.cur.v[e.kind] ?? 0)
      e.m.opacity = on ? e.base * k : 0
      e.obj.visible = e.m.opacity > 0.003
    }
  })
  const b = limitesPlan(), top = P.length ? P[P.length - 1].cur.y + altura(P.length - 1) : FLOOR_H
  caja.scale.set(b.maxX - b.minX + 0.4, top + 0.2, b.maxY - b.minY + 0.4)
  caja.position.set((b.minX + b.maxX) / 2, (top + 0.2) / 2 - 0.1, -(b.minY + b.maxY) / 2)
  caja.visible = S.detalle
  rotPlanta.forEach((sp) => { const F = P[sp.userData.fi]; sp.position.set(sp.userData.x, F.cur.y + altura(F.fi) / 2, sp.userData.z); sp.material.opacity = S.foco === 'all' && !HOUSE ? OP.floorLabel : 0; sp.visible = S.foco === 'all' && !HOUSE })
  // etiquetas P1, P2... de las plantas fantasma
  P.forEach((F) => {
    const fantasma = !HOUSE && S.foco !== 'all' && !enfocada(F.fi) && !(F.fi > S.foco && S.ocultarSup)
    F.tag.position.set(F.tag.userData.x, F.cur.y + altura(F.fi), F.tag.userData.z)
    F.tag.material.opacity = fantasma ? OP.ghostTag : 0; F.tag.visible = fantasma
  })
  // escaleras: completas si la planta enfocada es la de abajo o la de arriba (o todas); si no, segun Context
  escG.forEach(({ grp, etq, mats, H, E, cx, soloHuella }) => {
    const y0 = P[E.fi].cur.y, gap = P[E.fi + 1].cur.y - y0
    grp.position.y = y0; grp.scale.y = gap / H
    const k = !S.capas.stairs ? 0 : (S.foco === 'all' || S.foco === E.fi || S.foco === E.fi + 1) ? 1 : (E.fi + 1 < S.foco ? S.ctx * 0.7 : S.ocultarSup ? 0 : S.ctx * 0.7)
    mats.forEach(({ m, base }) => { m.opacity = base * k })
    grp.visible = k > 0.003
    if (etq) { etq.position.copy(m3(cx[0], cx[1], y0 + (soloHuella ? 0.3 : gap + 0.25))); etq.material.opacity = 0.9 * k; etq.visible = !HOUSE && k > 0.05 }
  })
  lineasExplosion()
  dibujarRuta3D()
}

// Rotulos. Calmado: solo planta enfocada, 70 %, con colisiones (gana la sala mas grande; la oculta sale al pasar el raton).
// ALL: rotulos de todas las plantas, con colisiones. Detalle: en todas las plantas, sin ocultar.
const _v = new THREE.Vector3(), _w = new THREE.Vector3()
function rotulos() {
  const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight, cajas = [], cand = []
  P.forEach((F) => F.labels.forEach((sp) => {
    const { room } = sp.userData
    let ok = S.capas.labels && visibleSala(room) && !!sp.material.map
    ok = ok && (S.detalle || S.foco === 'all' ? F.cur.v.slab > 0.5 : enfocada(F.fi)) // ALL: rotulos de todas las plantas, con colisiones
    if (HOUSE) ok = ok && etiquetaCasa(F.fi, room)
    if (ok) cand.push(sp); else { sp.visible = false; sp.material.opacity = 0; sp.userData.oculto = false }
  }))
  cand.sort((a, b) => b.userData.area - a.userData.area)
  for (const sp of cand) {
    const F = P[sp.userData.fi], hov = esHover(sp.userData.fi, sp.userData.room) || esSel(sp.userData.fi, sp.userData.room.id)
    let visible = true
    if (!S.detalle) {
      sp.getWorldPosition(_w); _v.copy(_w).project(camara)
      const cx = ((_v.x + 1) / 2) * w, cy = ((1 - _v.y) / 2) * h
      const pxPorM = h / (2 * camara.position.distanceTo(_w) * Math.tan((camara.fov * Math.PI) / 360))
      const bw = sp.scale.x * pxPorM, bh = sp.scale.y * pxPorM, r = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]
      if (cajas.some((q) => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1]) && !hov) visible = false
      if (visible) cajas.push(r)
    }
    sp.userData.oculto = !visible
    sp.material.opacity = visible ? (hov ? 1 : OP.label3d) * Math.min(1, F.cur.v.slab) : 0
    sp.visible = sp.material.opacity > 0.01
  }
}
function setFoco(v, f2d = true) {
  S.foco = v
  if (v !== 'all' && f2d) S.f2d = v
  objetivos()
  selectoresPlanta(); render2D(); tabla()
}

// color y opacidad base de las losas segun modo, hover y seleccion
function estiloLosas() {
  P.forEach((F) => F.slabs.forEach(({ r, losa, rayas }) => {
    const sel = esSel(F.fi, r.id), hov = esHover(F.fi, r), e = F.ent.find((x) => x.obj === losa)
    const capaDorm = S.color !== 'evac' && S.capas.sleeping && esDormitorio(r), capaIgn = S.color !== 'evac' && S.capas.ignition && esIgnicion(r)
    losa.material.color.set(sel ? T.sel : capaDorm ? T.outer : capaIgn ? T.stairs : colorSala(F.fi, r))
    e.base = sel ? OP.slabSel : S.color === 'evac' ? OP.slabHeat : capaDorm || capaIgn ? OP.slab * 2.4 : OP.slab
    if (hov && !sel) { e.base = S.color === 'evac' ? Math.min(0.9, OP.slabHeat + 0.2) : OP.slabHover; losa.material.color.offsetHSL(0, 0, 0.12) }
    if (rayas) F.ent.find((x) => x.obj === rayas).base = S.color === 'evac' ? OP.stairs * 0.5 : OP.stairs
  }))
}
function estilo3D() {
  if (!P.length) return
  estiloLosas()
  P.forEach((F) => {
    F.doors.forEach(({ o, trazo, col }) => trazo.material.color.set(esSel(F.fi, o.id) ? T.sel : col))
    F.labels.forEach((sp) => {
      const { t, aspecto, alto } = texturaRotulo(textoEtiqueta(F.fi, sp.userData.room))
      if (sp.material.map) sp.material.map.dispose()
      sp.material.map = t; sp.material.needsUpdate = true
      const h = 0.26 * alto; legible(sp, h * aspecto, h, (HOUSE ? 15 : PX_LINEA) * alto)
    })
  })
  aplicarPlantas(); rotSucio = true
  leyendas()
}

// Ruta de la habitacion seleccionada: blanca, con resplandor
function dibujarRuta3D() {
  if (!rutaG) return
  while (rutaG.children.length) { const o = rutaG.children.pop(); o.geometry.dispose(); o.material.dispose() }
  const s = S.sel
  if (!s || s.kind !== 'room' || !S.capas.route) return
  const ruta = rutaSala(s.fi, salaPorId(s.fi, s.id))
  if (!ruta) return
  const y = (fi) => (P[fi] ? P[fi].cur.y : plantaY(fi)), col = HOUSE ? '#ffffff' : T.sel // casa: ruta de la sala tocada en blanco
  const pts = ruta.puntos.map((p) => m3(p.x, p.y, y(p.fi) + 0.25))
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1], len = a.distanceTo(b)
    if (len < 1e-4) continue
    const cil = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, len, 8), new THREE.MeshBasicMaterial({ color: C(col), depthTest: false, transparent: true, fog: false }))
    cil.position.copy(a).add(b).multiplyScalar(0.5); cil.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize()); cil.renderOrder = 20
    rutaG.add(cil)
  }
  const bola = (p, r) => { const m = new THREE.Mesh(new THREE.SphereGeometry(r, 14, 10), new THREE.MeshBasicMaterial({ color: C(col), depthTest: false, transparent: true, fog: false })); m.position.copy(p); m.renderOrder = 21; rutaG.add(m) }
  bola(pts[0], 0.12); bola(pts[pts.length - 1], 0.18)
}

function objetivosRayo() {
  return P.filter((F) => F.cur.v.slab > 0.5).flatMap((F) => [...((S.detalle || enfocada(F.fi)) ? F.picks.filter((p) => S.capas[p.userData.capa]) : []), ...F.slabs.filter((s) => visibleSala(s.r)).map((s) => s.losa)])
}
function rayoEn(e) {
  const r = renderer.domElement.getBoundingClientRect()
  raycaster.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camara)
  return raycaster.intersectObjects(objetivosRayo(), false)[0]
}
function recoger(e) { const hit = rayoEn(e); if (hit) seleccionar(hit.object.userData.entidad); else if (HOUSE && S.sel) seleccionar(null) }
function sobrevolar(e) { const hit = rayoEn(e), ent = hit && hit.object.userData.entidad; ponerHover(ent && ent.kind === 'room' ? ent : null) }
// hover comun al 3D y al 2D: la habitacion sube de luminosidad y su rotulo aparece aunque estuviera oculto por colision
function ponerHover(ent) {
  if (HOUSE) return // sin comportamiento solo al pasar el raton
  const a = S.hover
  if ((!a && !ent) || (a && ent && a.id === ent.id && a.fi === ent.fi)) return
  S.hover = ent
  if (renderer) renderer.domElement.style.cursor = ent ? 'pointer' : ''
  estiloLosas(); aplicarPlantas(); rotSucio = true; render2D()
}

function regla() {
  if (!camara || !P.length) return
  const el = $('escala'), d = camara.position.distanceTo(controles.target), mpp = (2 * d * Math.tan((camara.fov * Math.PI) / 360)) / (renderer.domElement.clientHeight || 1)
  const bruto = 110 * mpp, pot = Math.pow(10, Math.floor(Math.log10(bruto)))
  const paso = [1, 2, 5, 10].map((k) => k * pot).find((v) => v >= bruto * 0.7) || 10 * pot
  el.querySelector('.regla').style.width = Math.round(paso / mpp) + 'px'
  el.querySelector('span').textContent = `${Math.round(paso * FT)} ft (${paso >= 1 ? paso : paso.toFixed(1)} m) · at view center`
}
function avisoHud() {
  if (!S.plan) return
  // MDT (split): sin la franja de modo/FPS; la altura estimada queda como tooltip
  if (SPLIT) {
    const txt = `Top floor height ${FLOOR_H} m is an estimate (floor levels derived from door heights).`
    $('aviso3d').innerHTML = `<span class="aviso" title="${txt}" aria-label="${txt}">ⓘ EST</span>`
    return
  }
  $('aviso3d').innerHTML = `${S.efectosReducidos ? 'REDUCED EFFECTS (LOW FRAME RATE) · ' : ''}${S.detalle ? 'DETAIL' : 'CALM'} · LEVELS FROM DOOR HEIGHTS · TOP FLOOR ${pies(FLOOR_H).toUpperCase()} (${FLOOR_H} M) <span class="aviso">ESTIMATE</span> · ${S.fps} FPS`
}

// ---------------------------------------------------------------- limites
function limitesPiso(f) {
  const xs = [], ys = []
  f.walls.forEach((w) => w.points.forEach((p) => { xs.push(p[0]); ys.push(p[1]) }))
  f.rooms.forEach((r) => { if (poligonoValido(r)) r.polygon.forEach((p) => { xs.push(p[0]); ys.push(p[1]) }); else if (r.bbox) { xs.push(r.bbox[0], r.bbox[2]); ys.push(r.bbox[1], r.bbox[3]) } })
  return xs.length ? { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) } : { minX: 0, maxX: 10, minY: 0, maxY: 10 }
}
function limitesPlan() {
  const bs = S.plan.floors.map(limitesPiso)
  return { minX: Math.min(...bs.map((b) => b.minX)), maxX: Math.max(...bs.map((b) => b.maxX)), minY: Math.min(...bs.map((b) => b.minY)), maxY: Math.max(...bs.map((b) => b.maxY)) }
}

// ---------------------------------------------------------------- 2D: mismo sistema de color y jerarquia
const NS = 'http://www.w3.org/2000/svg'
const anillo = (pts) => 'M' + pts.map((p) => `${p[0]} ${-p[1]}`).join('L') + 'Z'
function render2D() {
  if (!S.plan) return
  const fi = focoIdx(), f = piso(fi), cont = $('vista2d')
  if (!S.vb) { const b = limitesPlan(), p = 1.2; S.vb = [b.minX - p, -b.maxY - p, b.maxX - b.minX + 2 * p, b.maxY - b.minY + 2 * p] }
  const ns = 'vector-effect="non-scaling-stroke"'
  let h = `<defs><filter id="brillo" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="${FX.glow2d * 1.6}" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>` +
    `<pattern id="rayado" patternUnits="userSpaceOnUse" width="${FX.hatch}" height="${FX.hatch}" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="${FX.hatch}" stroke="${T.stairs}" stroke-width="${FX.hatch * 0.22}" stroke-opacity="${OP.stairs}"/></pattern></defs>`
  // plantas no enfocadas (mismo criterio que el 3D, escalado por Context): por debajo, contorno exterior marcado,
  // interiores muy tenues y losa casi invisible; por encima, solo el contorno exterior
  const c = S.ctx, vb0 = S.vb, fsTag = Math.max(0.18, 10 * Math.max(vb0[2] / (cont.clientWidth || 400), vb0[3] / (cont.clientHeight || 300)))
  S.plan.floors.forEach((g, gi) => {
    if (gi === fi || !S.capas.walls) return
    const abajo = gi < fi, ext = abajo ? c : S.ocultarSup ? 0 : c * 0.55, int = abajo ? c * 0.2 : 0, losa = abajo ? c * 0.12 : 0
    if (ext <= 0) return
    let gh = ''
    if (losa > 0 && S.capas.slabs) gh += salasDe(g).filter(poligonoValido).map((r) => `<path d="${anillo(r.polygon)}" fill="${T.slab}" fill-opacity="${losa}"/>`).join('')
    if (int > 0) gh += S.muros[gi].filter((m) => !m.ext).map((m) => `<line x1="${m.a[0]}" y1="${-m.a[1]}" x2="${m.b[0]}" y2="${-m.b[1]}" stroke="${T.inner}" stroke-opacity="${int}" stroke-width="1" ${ns}/>`).join('')
    gh += S.muros[gi].filter((m) => m.ext).map((m) => `<line x1="${m.a[0]}" y1="${-m.a[1]}" x2="${m.b[0]}" y2="${-m.b[1]}" stroke="${T.outer}" stroke-opacity="${ext}" stroke-width="1.2" stroke-dasharray="${abajo ? '' : '4 3'}" ${ns}/>`).join('')
    const bp = limitesPiso(g)
    if (!HOUSE) gh += `<text x="${bp.minX}" y="${-bp.maxY - fsTag * 0.3}" font-size="${fsTag}" font-family="${T.font}" fill="${T.textDim}" opacity="${OP.ghostTag}" pointer-events="none">P${gi + 1}</text>`
    h += `<g pointer-events="none">${gh}</g>`
  })
  const salas = salasDe(f).filter(visibleSala).sort((a, b) => (b.areaM2 || 0) - (a.areaM2 || 0))
  if (S.capas.slabs) salas.forEach((r) => {
    if (!poligonoValido(r)) return
    const d = anillo(r.polygon) + (r.holes || []).map(anillo).join(''), sel = esSel(fi, r.id), hov = esHover(fi, r)
    const op = sel ? OP.slabSel + 0.08 : S.color === 'evac' ? OP.slabHeat + (hov ? 0.2 : 0) : hov ? OP.slabHover + 0.05 : OP.slab + 0.04
    h += `<path class="ent" data-kind="room" data-id="${esc(r.id)}" d="${d}" fill="${sel ? T.sel : colorSala(fi, r)}" fill-opacity="${op}" fill-rule="evenodd" ${sel ? `stroke="${T.sel}" stroke-width="1.6" ${ns} filter="url(#brillo)"` : ''}>${HOUSE ? '' : `<title>${esc(nombreSala(r))}</title>`}</path>`
    if (esEscalera(r)) h += `<path d="${d}" fill="url(#rayado)" fill-rule="evenodd" pointer-events="none"/>`
  })
  // muros unidos: exteriores brillantes y mas gruesos, interiores medios
  if (S.capas.walls) {
    h += S.muros[fi].filter((m) => !m.ext).map((m) => `<line x1="${m.a[0]}" y1="${-m.a[1]}" x2="${m.b[0]}" y2="${-m.b[1]}" stroke="${T.inner}" stroke-width="1" ${ns}/>`).join('')
    h += S.muros[fi].filter((m) => m.ext).map((m) => `<line x1="${m.a[0]}" y1="${-m.a[1]}" x2="${m.b[0]}" y2="${-m.b[1]}" stroke="${T.outer}" stroke-width="1.8" stroke-linecap="square" ${ns}/>`).join('')
  }
  f.openings.filter((o) => o.type === 'doorway' || o.type === 'window').forEach((o) => {
    const esV = o.type === 'window'
    if (esV ? !S.capas.windows : !S.capas.doors) return
    const t = o.wallThickness || 0.1, w = o.width, sel = esSel(fi, o.id), col = sel ? T.sel : esV ? T.window : T.door
    let s = `<rect x="${-w / 2}" y="${-(t / 2 + 0.03)}" width="${w}" height="${t + 0.06}" fill="${T.bg0}" class="ent"/>`
    if (esV) s += `<line x1="${-w / 2}" y1="0" x2="${w / 2}" y2="0" stroke="${col}" stroke-opacity="${OP.window}" stroke-width="1.2" stroke-dasharray="3 2" ${ns}/>`
    else {
      s += `<line x1="${-w / 2}" y1="${-t / 2}" x2="${-w / 2}" y2="${t / 2}" stroke="${col}" stroke-width="1.2" ${ns}/><line x1="${w / 2}" y1="${-t / 2}" x2="${w / 2}" y2="${t / 2}" stroke="${col}" stroke-width="1.2" ${ns}/>`
      if (w <= 1.3) s += `<path d="M${-w / 2} ${-w}L${-w / 2} 0M${-w / 2} ${-w}A${w} ${w} 0 0 1 ${w / 2} 0" fill="none" stroke="${col}" stroke-opacity="${OP.doorArc + 0.15}" stroke-width="0.9" ${ns}/>`
    }
    h += `<g data-kind="${esV ? 'window' : 'door'}" data-id="${esc(o.id)}" transform="translate(${o.center[0]} ${-o.center[1]}) rotate(${-o.angleDeg})" ${sel ? 'filter="url(#brillo)"' : ''}>${HOUSE ? '' : `<title>${esV ? 'Window' : esSalida(fi, o) ? 'Exterior door (exit)' : 'Door'}</title>`}${s}</g>`
  })
  // capas Sleeping areas (tinte y cama) e Ignition areas (rayado)
  if (S.capas.sleeping) salas.filter(esDormitorio).forEach((r) => { const c = centroSala(r); h += `<path d="${anillo(r.polygon)}" fill="${T.outer}" fill-opacity="0.13" pointer-events="none"/>` + (c ? `<g transform="translate(${c[0]} ${-c[1] + 0.55})" opacity="0.7" pointer-events="none"><rect x="-0.32" y="-0.12" width="0.64" height="0.24" rx="0.04" fill="none" stroke="${T.outer}" stroke-width="1.2" ${ns}/><rect x="-0.3" y="-0.1" width="0.16" height="0.2" fill="${T.outer}"/></g>` : '') })
  if (S.capas.ignition) salas.filter(esIgnicion).forEach((r) => { h += `<path d="${anillo(r.polygon)}" fill="url(#rayado)" pointer-events="none"/>` })
  if (S.salidaVista && S.salidaVista.fi === fi) { const o = f.openings.find((x) => x.id === S.salidaVista.id); if (o) h += `<circle cx="${o.center[0]}" cy="${-o.center[1]}" r="0.55" fill="none" stroke="${T.sel}" stroke-width="2.5" ${ns} filter="url(#brillo)"><animate attributeName="r" values="0.45;0.75;0.45" dur="1.4s" repeatCount="indefinite"/></circle>` }
  // rotulos: nombre o tipo (o la distancia), 70 %, con colisiones (gana la sala mas grande); al tocar una sala su rotulo aparece
  if (S.capas.labels) {
    const vb = S.vb, escPx = Math.max(vb[2] / (cont.clientWidth || 400), vb[3] / (cont.clientHeight || 300)), fs = Math.max(0.16, (HOUSE ? 10 : 12) * escPx), ocupado = []
    salas.forEach((r) => {
      if (HOUSE ? !etiquetaCasa(fi, r) : (r.areaM2 || 0) < SALA_MIN && !esEscalera(r)) return
      const c = centroSala(r); if (!c) return
      const [l1, l2] = textoEtiqueta(fi, r), hov = esHover(fi, r) || esSel(fi, r.id)
      const ancho = Math.max(l1.length, (l2 || '').length * 0.8) * fs * 0.66, alto = fs * (l2 ? 2.3 : 1.2)
      const caja2 = [c[0] - ancho / 2, -c[1] - fs, c[0] + ancho / 2, -c[1] - fs + alto]
      if (!S.detalle && !hov && ocupado.some((q) => caja2[0] < q[2] && caja2[2] > q[0] && caja2[1] < q[3] && caja2[3] > q[1])) return
      ocupado.push(caja2)
      const comun = `font-family="${T.font}" text-anchor="middle" letter-spacing="${fs * 0.08}" opacity="${hov ? 1 : OP.label}" pointer-events="none"`
      h += `<text x="${c[0]}" y="${-c[1]}" font-size="${fs}" fill="${T.text}" ${comun}>${esc(l1.toUpperCase())}</text>`
      if (l2) h += `<text x="${c[0]}" y="${-c[1] + fs * 1.2}" font-size="${fs}" fill="${l2 === SIN_RUTA ? T.noRoute : T.textDim}" ${comun}>${esc(l2.toUpperCase())}</text>`
    })
  }
  // escaleras (mismo ajuste que el 3D): peldanos y flecha dentro del rectangulo que cabe en su sala y en la casa.
  // Si no cabe o la sala es dudosa: solo la huella discontinua y "escalera: ubicación no confirmada".
  if (S.capas.stairs) S.escaleras.filter((E) => E.fi === fi || E.fi + 1 === fi).forEach((E) => {
    const e = E.e, R = E.rect, L = R.s1 - R.s0, pt = (sv, tv) => [e.m[0] + e.u[0] * sv + e.v[0] * tv, -(e.m[1] + e.u[1] * sv + e.v[1] * tv)]
    const op = E.fi === fi ? 1 : 0.55, dash = E.conf === 'clara' && E.estado === 'ok' ? '' : 'stroke-dasharray="4 3"'
    const fs = Math.max(0.11, 6.5 * Math.max(S.vb[2] / (cont.clientWidth || 400), S.vb[3] / (cont.clientHeight || 300)))
    const rotulo = (txt, p) => HOUSE ? '' : `<text x="${p[0]}" y="${p[1]}" font-size="${fs}" font-family="${T.font}" text-anchor="middle" fill="${T.stairs}" opacity="${op * 0.9}">${txt}</text>`
    let g = ''
    if (E.estado !== 'ok') {
      g += `<path d="${anillo(E.r.polygon)}" fill="none" stroke="${T.stairs}" stroke-opacity="${op}" stroke-width="1.2" stroke-dasharray="4 3" ${ns}/>`
      const c0 = centroide(E.r.polygon); g += rotulo('ESCALERA: UBICACIÓN NO CONFIRMADA', [c0[0], -c0[1] + fs * 2.6])
    } else {
      g += `<path d="M${[[R.s0, R.t0], [R.s1, R.t0], [R.s1, R.t1], [R.s0, R.t1]].map(([sv, tv]) => pt(sv, tv).join(' ')).join('L')}Z" fill="none" stroke="${T.stairs}" stroke-opacity="${0.7 * op}" stroke-width="1" ${dash} ${ns}/>`
      if (E.dir) {
        const n = E.peldanos || Math.max(3, Math.round(((S.niveles[E.fi + 1] - S.niveles[E.fi]) || FLOOR_H) / FX.riser))
        for (let i = 1; i < n; i++) { const sv = R.s0 + (L * i) / n, a = pt(sv, R.t0), b = pt(sv, R.t1); g += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${T.stairs}" stroke-opacity="${0.5 * op}" stroke-width="0.8" ${ns}/>` }
        const ini = E.dir > 0 ? R.s0 + L * 0.08 : R.s1 - L * 0.08, fin = E.dir > 0 ? R.s1 - L * 0.08 : R.s0 + L * 0.08
        const a = pt(ini, R.tc), b = pt(fin, R.tc), ang = Math.atan2(b[1] - a[1], b[0] - a[0]), k = Math.min(0.3, L * 0.15)
        if (E.conf !== 'clara') g += rotulo('DIRECCIÓN ESTIMADA', [pt((R.s0 + R.s1) / 2, R.t1)[0], pt((R.s0 + R.s1) / 2, R.t1)[1] - fs * 0.5])
        g += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${T.stairs}" stroke-opacity="${op}" stroke-width="1.4" ${dash} ${ns}/>` +
          `<path d="M${b[0] - k * Math.cos(ang - 0.45)} ${b[1] - k * Math.sin(ang - 0.45)}L${b[0]} ${b[1]}L${b[0] - k * Math.cos(ang + 0.45)} ${b[1] - k * Math.sin(ang + 0.45)}" fill="none" stroke="${T.stairs}" stroke-opacity="${op}" stroke-width="1.4" ${ns}/>` +
          `<circle cx="${a[0]}" cy="${a[1]}" r="${Math.min(0.08, L * 0.05)}" fill="${T.stairs}" fill-opacity="${op}"/>`
      } else g += rotulo('DIRECCIÓN NO CONFIRMADA', [pt((R.s0 + R.s1) / 2, R.tc)[0], pt((R.s0 + R.s1) / 2, R.tc)[1] + fs * 2.6])
    }
    const motivo = E.estado === 'dudosa' ? 'location not confirmed: room outline unreliable' : E.estado === 'no_cabe' ? 'location not confirmed: footprint does not fit inside its room and the house even at 70 %' :
      (E.conf === 'clara' ? 'direction from entries at both ends' : 'direction estimated: ' + E.motivo) + (E.escala < 1 ? ' · footprint reduced to ' + Math.round(E.escala * 100) + ' %' : '')
    h += `<g pointer-events="none">${HOUSE ? '' : `<title>Stairs (${esc(motivo)})</title>`}${g}</g>`
  })
  // ruta de la habitacion seleccionada: blanca con resplandor
  const sel = S.sel
  if (sel && sel.kind === 'room' && S.capas.route) {
    const ruta = rutaSala(sel.fi, salaPorId(sel.fi, sel.id))
    if (ruta) {
      const sc = HOUSE ? '#ffffff' : T.sel // casa: ruta de la sala tocada en blanco
      const pts = ruta.puntos.filter((p) => p.fi === fi)
      if (pts.length > 1) h += `<polyline points="${pts.map((p) => `${p.x},${-p.y}`).join(' ')}" fill="none" stroke="${sc}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" filter="url(#brillo)" pointer-events="none" data-route="tap" ${ns}/>`
      if (ruta.puntos[0].fi === fi) h += `<circle cx="${ruta.puntos[0].x}" cy="${-ruta.puntos[0].y}" r="0.12" fill="${sc}" filter="url(#brillo)"/>`
      const fin = ruta.puntos[ruta.puntos.length - 1]; if (fin.fi === fi) h += `<circle cx="${fin.x}" cy="${-fin.y}" r="0.18" fill="none" stroke="${sc}" stroke-width="1.6" ${ns} filter="url(#brillo)"/>`
    }
  }
  h += svgIncidente2D(fi, ns)
  cont.innerHTML = `<svg xmlns="${NS}" viewBox="${S.vb.join(' ')}" preserveAspectRatio="xMidYMid meet">${h}</svg>`
}
function selectoresPlanta() {
  if (!S.plan) return
  const botones = `<span class="etq">Floor</span><button data-foco="all" class="${S.foco === 'all' ? 'on' : ''}" title="All floors">ALL</button>` +
    S.plan.floors.map((f, i) => `<button data-foco="${i}" class="${S.foco === i ? 'on' : ''}" title="${esc(f.label)}">${i + 1}</button>`).join('')
  $('plantas3d').innerHTML = botones
  // mini selector: plantas apiladas como barras (la mas alta arriba)
  $('minipisos').innerHTML = `<button data-foco="all" class="barra ${S.foco === 'all' ? 'on' : ''}">ALL</button>` +
    S.plan.floors.map((f, i) => i).reverse().map((i) => `<button data-foco="${i}" class="barra ${S.foco === i ? 'on' : ''}" title="${esc(piso(i).label)}">P${i + 1}</button>`).join('')
  $('tabs2d').innerHTML = botones + `<span class="hint" style="margin-left:6px">2D: ${esc(piso(focoIdx()).label)}</span><button id="fit2d" title="Fit" style="margin-left:auto">⤢</button>`
  barraSplit()
}
// ---------------------------------------------------------------- modo split: barra comun (planta, color, capas) y paneles plegables
// modo compacto: pantalla de 1180 px o menos (en la UI se mira el ancho de la pagina anfitriona)
function anchoPantalla() { try { return HOST && window.parent !== window ? window.parent.innerWidth : window.innerWidth } catch (e) { return window.innerWidth } }
function aplicarCompacto() { document.documentElement.classList.toggle('compacto', SPLIT && anchoPantalla() <= 1180); inspector() }
function montarSplit() {
  const barra = document.createElement('div')
  barra.id = 'barra-split'
  barra.innerHTML = '<span class="etq">Floor</span><span id="pisos-split" class="plantas"></span><span id="planta2d-split" class="hint"></span>' +
    '<span class="hueco"></span><button data-fit title="Fit the 2D plan">⤢ Fit 2D</button><button id="btn-capas" aria-expanded="false">Layers ▾</button>'
  document.querySelector('main').prepend(barra)
  barra.appendChild($('izq')) // el panel de vista/capas/filtros pasa a ser un menu plegable
  const plegable = (id, titulo, abierto, hijo) => {
    const d = document.createElement('details'); d.id = id; d.className = 'plegable'; d.open = abierto
    d.innerHTML = `<summary>${titulo}</summary>`; d.appendChild(hijo); $('der').appendChild(d); return d
  }
  $('der').appendChild($('leyenda2d'))
  plegable('insp-split', 'Inspector', true, $('inspector'))
  const pest = document.createElement('div'); pest.id = 'der-tabs'; pest.className = 'fila-btn'
  pest.innerHTML = '<button data-der="plan" class="on">Plan</button><button data-der="entorno">Surroundings</button>'
  $('der').insertBefore(pest, $('der').firstChild)
  pest.addEventListener('click', (e) => { const b = e.target.closest('[data-der]'); if (!b) return
    document.documentElement.classList.toggle('der-entorno', b.dataset.der === 'entorno')
    pest.querySelectorAll('[data-der]').forEach((x) => x.classList.toggle('on', x === b)) })
  window.addEventListener('resize', aplicarCompacto); aplicarCompacto()
  plegable('tabla-split', 'Entities', false, $('tabla'))
  const menu = (abrir) => { $('izq').classList.toggle('abierto', abrir); $('btn-capas').setAttribute('aria-expanded', String(abrir)); $('btn-capas').classList.toggle('on', abrir) }
  barra.addEventListener('click', (e) => {
    const t = e.target.closest('button'); if (!t || $('izq').contains(t)) return
    if (t.id === 'btn-capas') return menu(!$('izq').classList.contains('abierto'))
    if (t.dataset.foco != null) setFoco(t.dataset.foco === 'all' ? 'all' : Number(t.dataset.foco))
    if (t.dataset.fit != null) { S.vb = null; render2D() }
    if (t.dataset.color && S.plan) { S.color = t.dataset.color; panelIzq(); refrescar() }
  })
  window.addEventListener('mousedown', (e) => { if (!barra.contains(e.target)) menu(false) })
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') menu(false) })
}
// layout=house: Surroundings siempre visible con un titulo fijo; aviso de una linea para "sin ruta"
function montarCasa() {
  // fuera del <details> (WebKit no estira su contenido): titulo fijo + cuerpo de las oblicuas
  const ent = document.createElement('div'); ent.id = 'casa-entorno'
  ent.innerHTML = '<div class="casa-tit">Surroundings</div>'; ent.appendChild($('oblicuas-body')); $('der').appendChild(ent)
  const av = document.createElement('div'); av.id = 'aviso-ruta'; av.hidden = true; av.setAttribute('role', 'status'); document.body.appendChild(av)
}
function barraSplit() {
  if (!SPLIT || !S.plan || !$('pisos-split')) return
  $('pisos-split').innerHTML = `<button data-foco="all" class="${S.foco === 'all' ? 'on' : ''}" title="All floors">ALL</button>` +
    S.plan.floors.map((f, i) => `<button data-foco="${i}" class="${S.foco === i ? 'on' : ''}" title="${esc(f.label)}">${i + 1}</button>`).join('')
  $('planta2d-split').textContent = '2D: ' + piso(focoIdx()).label
  document.querySelectorAll('#color-split [data-color]').forEach((b) => b.classList.toggle('on', b.dataset.color === S.color))
}
function zoomPan2D() {
  const cont = $('vista2d')
  cont.addEventListener('wheel', (e) => {
    e.preventDefault()
    if (!S.vb) return
    const vb = S.vb, r = cont.getBoundingClientRect(), k = e.deltaY < 0 ? 0.87 : 1 / 0.87
    const e2 = Math.max(vb[2] / r.width, vb[3] / r.height), cx = vb[0] + vb[2] / 2 + (e.clientX - r.left - r.width / 2) * e2, cy = vb[1] + vb[3] / 2 + (e.clientY - r.top - r.height / 2) * e2
    S.vb = [cx - (cx - vb[0]) * k, cy - (cy - vb[1]) * k, vb[2] * k, vb[3] * k]; render2D()
  }, { passive: false })
  let arr = null, movido = false, pinza = null
  const dedos = new Map()
  cont.addEventListener('pointerdown', (e) => {
    dedos.set(e.pointerId, [e.clientX, e.clientY])
    if (dedos.size === 2 && S.vb) { const [a, b] = [...dedos.values()]; pinza = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, vb: [...S.vb], m: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] }; arr = null; movido = true; return }
    if (S.vb) { arr = { x: e.clientX, y: e.clientY, vb: [...S.vb] }; movido = false }
  })
  window.addEventListener('pointermove', (e) => {
    if (dedos.has(e.pointerId)) dedos.set(e.pointerId, [e.clientX, e.clientY])
    if (pinza && dedos.size === 2) {
      const [a, b] = [...dedos.values()], k = pinza.d / (Math.hypot(a[0] - b[0], a[1] - b[1]) || 1), r = cont.getBoundingClientRect(), vb = pinza.vb
      const e2 = Math.max(vb[2] / r.width, vb[3] / r.height), cx = vb[0] + vb[2] / 2 + (pinza.m[0] - r.left - r.width / 2) * e2, cy = vb[1] + vb[3] / 2 + (pinza.m[1] - r.top - r.height / 2) * e2
      S.vb = [cx - (cx - vb[0]) * k, cy - (cy - vb[1]) * k, vb[2] * k, vb[3] * k]; render2D(); return
    }
    if (!arr) return
    const r = cont.getBoundingClientRect(), e2 = Math.max(arr.vb[2] / r.width, arr.vb[3] / r.height)
    if (Math.hypot(e.clientX - arr.x, e.clientY - arr.y) > 3) movido = true
    if (movido) { S.vb = [arr.vb[0] - (e.clientX - arr.x) * e2, arr.vb[1] - (e.clientY - arr.y) * e2, arr.vb[2], arr.vb[3]]; render2D() }
  })
  const soltar = (e) => { dedos.delete(e.pointerId); if (dedos.size < 2) pinza = null; arr = null }
  window.addEventListener('pointerup', soltar); window.addEventListener('pointercancel', soltar)
  cont.addEventListener('click', (e) => {
    if (movido) return
    const el = e.target.closest('[data-kind]')
    if (el) seleccionar({ kind: el.dataset.kind, id: el.dataset.id, fi: focoIdx() })
    else if (HOUSE && S.sel) seleccionar(null)
  })
  cont.addEventListener('mousemove', (e) => {
    if (arr) return
    const el = e.target.closest('[data-kind="room"]')
    ponerHover(el ? { kind: 'room', id: el.dataset.id, fi: focoIdx() } : null)
  })
  cont.addEventListener('mouseleave', () => ponerHover(null))
}

// ---------------------------------------------------------------- vistas oblicuas (Vexcel) bajo el plano 2D
// Solo las diagonales N/E/S/W (sin la vista cenital). Origen: API /api/house-viewer/vexcel/<sample> (mismo origen; la sirve
// el propio servidor de Vite con el plugin apps/web/server/house-data.mjs, que guarda la clave). Nada se guarda en local.
const OBL = { id: null, meta: null, base: '', dir: 'aerial', estado: '', error: '' }
const OBL_DIRS = [['aerial', 'Aerial'], ['north', 'N'], ['east', 'E'], ['south', 'S'], ['west', 'W']]
const OBL_ARCHIVO = /^(?:oblique-(?:north|east|south|west)|aerial)\.jpg$/
const OBL_API = '/api/house-viewer/vexcel/'
async function leerOblicuas(url) {
  const r = await fetch(url)
  const j = await r.json().catch(() => null)
  if (!r.ok || !j) return { error: (j && j.error) || (r.status >= 500 ? 'imagery server not reachable' : 'HTTP ' + r.status) }
  const vistas = Object.fromEntries(OBL_DIRS.map(([d]) => [d, j.views && j.views[d]]).filter(([, v]) => v && OBL_ARCHIVO.test(v.file || '')))
  return Object.keys(vistas).length ? { meta: { ...j, views: vistas } } : { error: 'no oblique views at this location' }
}
const dirsObl = () => (HOUSE ? OBL_DIRS.filter(([d]) => d !== 'aerial') : OBL_DIRS) // casa: solo N / E / S / W
if (HOUSE) OBL.dir = 'north'
async function cargarOblicuas(id) {
  OBL.id = id; OBL.meta = null; OBL.estado = 'cargando'; OBL.error = ''; pintarOblicuas()
  let res = null
  try { res = await leerOblicuas(OBL_API + encodeURIComponent(id)); if (res.meta) OBL.base = OBL_API + encodeURIComponent(id) + '/' } catch (e) { res = { error: 'imagery server not reachable' } }
  if (OBL.id !== id) return
  if (res.meta) { OBL.meta = res.meta; OBL.estado = 'ok'; if (!res.meta.views[OBL.dir]) OBL.dir = (dirsObl().find(([d]) => res.meta.views[d]) || [Object.keys(res.meta.views)[0]])[0] }
  else { OBL.estado = 'nada'; OBL.error = res.error || '' }
  pintarOblicuas()
}
function pintarOblicuas() {
  const el = $('oblicuas-body'); if (!el) return
  if (OBL.estado === 'cargando') { el.innerHTML = '<div class="obl-vacio">Loading oblique views…<br><span class="hint">The first time for a house can take about 10 s.</span></div>'; return }
  if (OBL.estado !== 'ok') {
    const ayuda = /not reachable/.test(OBL.error) ? 'Oblique views are served by the app server (npm run dev or npm run preview).' : OBL.error
    el.innerHTML = `<div class="obl-vacio">No oblique imagery for this sample.<br><span class="hint">${esc(ayuda)}</span></div>`; return
  }
  const m = OBL.meta, v = m.views[OBL.dir]
  const tabs = '<div class="obl-tabs fila-btn">' + dirsObl().map(([d, t]) => `<button data-obl="${d}" class="${d === OBL.dir ? 'on' : ''}">${t}</button>`).join('') + '</div>'
  if (!v) { el.innerHTML = tabs + '<div class="obl-vacio">No imagery available</div><div class="obl-pie hint">N / E / S / W: direction the camera faces.</div>'; return }
  const url = OBL.base + v.file, cenital = OBL.dir === 'aerial'
  if (HOUSE) { // una imagen sin enlace y una linea con la fecha de captura
    el.innerHTML = tabs + `<img class="obl-img" src="${esc(url)}" alt="Oblique aerial view, camera facing ${esc(OBL.dir)}">` +
      `<div class="obl-pie hint">Captured ${esc(v.captureDate ? fechaCorta(v.captureDate) : 'date not available')}</div>`
    return
  }
  el.innerHTML = tabs +
    `<a href="${esc(url)}" target="_blank" rel="noopener"><img class="obl-img" src="${esc(url)}" alt="${cenital ? 'Straight-down aerial photo' : `Oblique aerial view, camera facing ${esc(OBL.dir)}`}"></a>` +
    `<div class="obl-pie hint">Captured ${esc(v.captureDate ? fechaCorta(v.captureDate) : 'date not available')} · ${esc(m.copyright || '© Vexcel Imaging')} · ${cenital ? 'straight-down aerial photo' : 'aerial photos at an angle, not street level'}</div>` +
    '<div class="obl-pie hint">N / E / S / W: direction the camera faces.</div>'
}

// ---------------------------------------------------------------- leyendas (la misma en 3D y 2D)
function leyendaHTML(corta) {
  const it = (cls, estilo, t) => `<div class="it"><span class="sw ${cls}" style="${estilo}"></span>${t}</div>`
  const comun = it('', `background:${T.sel};box-shadow:0 0 6px ${T.sel}`, 'selection · route') + it('trazo', '', 'door') + it('disc', '', 'window') + it('rayado', '', 'stairs') + `<div class="it" style="grid-column:1/-1"><span class="sw" style="height:0;border:none;border-top:2px solid ${T.stairs}"></span>stair direction clear · <span class="sw" style="height:0;border:none;border-top:2px dashed ${T.stairs}"></span>estimated</div>`
  if (S.color === 'evac') {
    return `<div class="tit">Distance to nearest exit (estimate)</div><div class="barra"></div><div class="esc"><span>near exit · 0 ft</span><span>${piesM(S.ev.maxDist)} · far</span></div>` +
      `<div class="cols">${it('', `background:${T.noRoute}`, SIN_RUTA)}${comun}</div>`
  }
  return `${corta ? '' : '<div class="tit">Legend</div>'}<div class="cols">${it('', `height:0;border:none;border-top:2px solid ${T.outer}`, 'exterior wall')}${it('', `height:0;border:none;border-top:1px solid ${T.inner}`, 'interior wall')}` +
    `${it('', `background:${T.slab};opacity:.35`, 'room (same tint)')}${S.capas.sleeping ? it('', `background:${T.outer};opacity:.45`, 'sleeping area (bedroom)') : ''}${S.capas.ignition ? it('rayado', '', 'ignition area (kitchen, laundry, garage)') : ''}${comun}</div>`
}
function leyendas() { if (!S.ev) return; $('leyenda').innerHTML = leyendaHTML(false); $('leyenda2d').innerHTML = leyendaHTML(true) }

// ---------------------------------------------------------------- tabla de entidades
function entidades() {
  const out = []
  S.plan.floors.forEach((f, fi) => {
    if (S.foco !== 'all' && S.foco !== fi) return
    salasDe(f).filter(visibleSala).sort((a, b) => (b.areaM2 || 0) - (a.areaM2 || 0)).forEach((r) => out.push({ kind: 'room', id: r.id, fi, piso: f.label, tipo: r.type || 'untyped', nombre: r.label, tam: r.areaM2 != null ? areaTxt(r.areaM2) : 'Not available', cls: esEscalera(r) ? 'k-stairs' : 'k-room' }))
    if (S.capas.doors) f.openings.filter((o) => o.type === 'doorway').forEach((o) => out.push({ kind: 'door', id: o.id, fi, piso: f.label, tipo: esSalida(fi, o) ? 'doorway · exit' : 'doorway', tam: `${pies(o.width)} × ${o.height != null ? pies(o.height) : '?'}`, cls: esSalida(fi, o) ? 'k-exit' : 'k-door' }))
    if (S.capas.windows) f.openings.filter((o) => o.type === 'window').forEach((o) => out.push({ kind: 'window', id: o.id, fi, piso: f.label, tipo: 'window', tam: `${pies(o.width)} × ${o.height != null ? pies(o.height) : '?'}`, cls: 'k-window' }))
  })
  return out
}
function tabla() {
  if (!S.plan) return
  $('tabla').innerHTML = '<table><thead><tr><th>Entity</th><th>Floor</th><th>Type</th><th>Size</th><th>Source</th></tr></thead><tbody>' +
    entidades().map((e) => `<tr class="fila ${S.sel && S.sel.id === e.id ? 'sel' : ''}" data-kind="${e.kind}" data-id="${esc(e.id)}" data-fi="${e.fi}"><td class="${e.cls}">${e.kind === 'room' ? 'Room' : e.kind === 'door' ? 'Door' : 'Window'}${e.nombre ? ' · ' + esc(e.nombre) : ''}</td><td>${esc(e.piso)}</td><td>${esc(e.tipo)}</td><td>${esc(e.tam)}</td><td>Matterport API</td></tr>`).join('') +
    '</tbody></table>'
}

// ---------------------------------------------------------------- inspector y notas
const fila = (k, v) => (v == null || v === '' ? '' : `<tr><td>${esc(k)}</td><td>${v}</td></tr>`)
// "Over: kitchen · Next to: hallway, bathroom" from the relations stored in the plan (plan-extras.mjs); nothing recomputed
function relacionesTxt(r) {
  const nombre = (id) => { for (const f of S.plan.floors) { const x = f.rooms.find((y) => y.id === id); if (x) return (x.label || x.type || 'room').toLowerCase() } return null }
  const lista = (ids) => [...new Set((ids || []).map(nombre).filter(Boolean))].join(', ')
  const partes = [r.over && r.over.length ? `Over: ${lista(r.over)}` : '', r.next_to && r.next_to.length ? `Next to: ${lista(r.next_to)}` : ''].filter(Boolean)
  return partes.length ? `<tr><td colspan="2" class="relaciones">${esc(partes.join(' · '))}</td></tr>` : ''
}
const ESTADOS = { verified: ['●', 'VER', 'Verified'], inferred: ['◆', 'INF', 'Inferred'], unverified: ['○', 'UNV', 'Unverified'] }
const chip = (k, nota) => `<span class="st st-${k}" title="${esc(ESTADOS[k][2] + (nota ? ': ' + nota : ''))}"><span aria-hidden="true">${ESTADOS[k][0]}</span>${ESTADOS[k][1]}</span>`
function inspector() {
  const el = $('inspector'), s = S.sel
  if (HOUSE) { el.style.display = 'none'; return } // casa: tocar una sala no abre ningun panel
  const hoja = $('insp-split'); if (hoja) { hoja.classList.toggle('hoja-abierta', !!s); if (s && document.documentElement.classList.contains('compacto')) hoja.open = true } // hoja inferior en modo compacto (sin resumen: siempre abierta)
  if (!s) {
    if (SPLIT) { el.innerHTML = '<div class="hint">Select a room, door or window in either view.</div>'; el.style.display = 'block' } else el.style.display = 'none'
    return
  }
  let h = '<button class="cerrar" id="cerrar-insp">×</button>', t = ''
  const f = piso(s.fi)
  let dq = '' // seccion plegable "Data quality"
  if (s.kind === 'room') {
    const r = salaPorId(s.fi, s.id), ruta = rutaSala(s.fi, r)
    const salidaId = ruta ? ruta.puntos[ruta.puntos.length - 1].salida : null, salida = salidaId ? S.ev.puertas.get(salidaId) : null
    const descSalida = salida ? `exterior door in the ${esc(nombreSala(salaPorId(salida.fi, salida.conecta[0]) || { type: 'room' }))}` : null
    h += `<h3>${esc(nombreSala(r))}</h3><div class="hint">ROOM · ${esc(f.label.toUpperCase())}</div>`
    t = fila('Type', esc(r.type || 'Not available') + ' <span class="hint">detected by AI, can be wrong</span>') + fila('Area', r.areaM2 != null ? areaTxt(r.areaM2) : 'Not available') +
      fila('Distance to nearest exit', ruta ? `<b style="color:${T.sel}">≈ ${pies(ruta.dist)}</b>, estimate` : `<span style="color:${T.noRoute}">${SIN_RUTA}</span>`) +
      fila('Nearest exit', descSalida ? `${descSalida}<br><button id="ver-salida" class="ver-salida">Show on plan</button>` : '') + relacionesTxt(r)
    dq = fila('Outline', r.polygonQuality === 'ok' ? 'reliable' + chip('verified', 'outline passed validation') : `<span class="aviso">unreliable (no slab drawn)</span> ${esc((r.polygonIssues || []).join(', '))}` + chip('unverified', 'outline failed validation'))
  } else {
    const o = f.openings.find((x) => x.id === s.id), info = S.ev.puertas.get(o.id)
    const nombre = (id) => { const r = salaPorId(s.fi, id); return r ? esc(nombreSala(r)) : 'an unknown room' }
    h += `<h3>${s.kind === 'door' ? 'Door' : 'Window'}</h3><div class="hint">${esc(o.type.toUpperCase())} · ${esc(f.label.toUpperCase())}</div>`
    t = fila('Size', `${pies(o.width)} × ${o.height != null ? pies(o.height) : 'Not available'} (${o.width} × ${o.height ?? '?'} m)`) +
      fila('Sill height', o.lowerElevation == null ? 'Not available' : `${piesM(antepecho(o, baseElev(f)))} above this floor's level`)
    if (s.kind === 'door') t += fila('Connects', !info ? 'not placed between two known rooms' : info.salida ? `exterior door in the ${nombre(info.conecta[0])}` : info.exteriorUpper ? `${nombre(info.conecta[0])} ↔ outside (terrace or balcony, not counted as an exit)` : info.conecta.map(nombre).join(' ↔ '))
    dq = fila('Wall thickness', o.wallThickness != null ? piesM(o.wallThickness).replace(/^(\d+) ft/, (m, n) => `${(o.wallThickness * FT * 12).toFixed(0)} in`) : 'Not available')
  }
  const c = S.fila
  const calidad = !c ? 'Not available' : c.estado === 'ok'
    ? 'OK' + chip('verified', 'passed the house quality check') + (c.etiquetas ? '<br>' + c.etiquetas.split('|').filter(Boolean).map((x) => esc(TAG_TXT[x] || x)).join('<br>') : '')
    : `<span style="color:${T.noRoute}">Discarded</span><br>` + esc(c.motivo)
  const fuenteTxt = S.scan && S.scan.created ? `Matterport scan · model created ${esc(fechaCorta(S.scan.created))}` : 'Matterport scan · capture date not available'
  h += `<table>${t}</table><details class="calidad-datos"><summary>Data quality</summary><table>${dq}${fila('House quality check', calidad)}${fila('Source', fuenteTxt)}</table></details><div class="descargo">${esc(DESCARGO)}</div>`
  el.innerHTML = h; el.style.display = 'block'
}
function notas() {
  if (!$('notas')) return // MDT (split): sin panel de notas
  const ev = S.ev, pisos = S.plan.floors
  const todas = pisos.flatMap((f, fi) => salasDe(f).map((r) => ({ r, fi, f })))
  const lista = (arr) => arr.map(({ r, fi, f }) => { const ru = rutaSala(fi, r); return `<li>${esc(nombreSala(r))} <span class="hint">· ${esc(f.label)} · ${ru ? '≈ ' + pies(ru.dist) + ' to nearest exit (est.)' : SIN_RUTA}</span></li>` }).join('')
  const dorm = todas.filter(({ r }) => esDormitorio(r)), ign = todas.filter(({ r }) => esIgnicion(r))
  let h = '<h2>Notes (no scoring)</h2>'
  if (ev.sinDatosEscaleras) h += '<div class="nota"><b>no staircase data</b> — no staircase was detected in this multi-floor plan, so routes between floors cannot be calculated and upper floors show "${SIN_RUTA}".</div>'
  else if (pisos.length >= 2) h += `<div class="nota">${todas.filter(({ r }) => esEscalera(r)).length} staircase room(s) detected; ${ev.escalerasEnlazadas} link(s) between floors.</div>`
  h += `<div class="nota"><b>BEDROOMS (${dorm.length})</b>${dorm.length ? '<ul>' + lista(dorm) + '</ul>' : '<br>No bedrooms detected.<br>'}<b>POSSIBLE IGNITION AREAS (${ign.length})</b>${ign.length ? '<ul>' + lista(ign) + '</ul>' : '<br>None detected.<br>'}` +
    '<span class="hint">Suggestion: you may want to check on site whether these rooms have a clear way out and working smoke detection; the plan has no information about either.</span></div>'
  h += `<div class="nota hint">Exits: ${ev.salidas.length} exterior door(s) on the lowest floor (a door whose other side, 0.3 m away, is outside every known room). Distances are straight lines between room and door centers: an estimate that ignores furniture and the real walking path. Floor height ${piesM(FLOOR_H)} is an estimate. Rooms whose only way out is through a patio or an undetected door get no route.</div>`
  h += `<div class="nota">${esc(DESCARGO)}</div>`
  $('notas').innerHTML = h
}

// ---------------------------------------------------------------- panel izquierdo
function panelIzq() {
  const tipos = {}
  S.plan.floors.forEach((f) => salasDe(f).forEach((r) => { tipos[tipoKey(r)] = (tipos[tipoKey(r)] || 0) + 1 }))
  const ord = Object.keys(tipos).sort((a, b) => tipos[b] - tipos[a])
  const capa = (k, txt) => `<label class="fila"><input type="checkbox" data-capa="${k}" ${S.capas[k] ? 'checked' : ''}> ${txt}</label>`
  // MDT (split): menu reducido a escaleras, puertas, ruta y mapa de calor; muros, losas, etiquetas y ventanas siguen visibles sin interruptor
  if (SPLIT) {
    $('izq').innerHTML = '<h2>Layers</h2>' + capa('stairs', 'Stairs') + capa('doors', 'Doors') + capa('route', 'Route of the selected room') + capa('sleeping', 'Sleeping areas') + capa('ignition', 'Ignition areas') + capa('incident', 'Incident layer') +
      `<label class="fila"><input type="checkbox" id="heat" ${S.color === 'evac' ? 'checked' : ''}> Evacuation heat map</label>`
    return
  }
  $('izq').innerHTML = '<h2>View</h2>' +
    `<div class="fila-btn"><button data-modo="calm" class="${!S.detalle ? 'on' : ''}">Calm</button><button data-modo="detail" class="${S.detalle ? 'on' : ''}">Detail</button></div>` +
    `<div class="fila-btn"><button data-color="type" class="${S.color === 'type' ? 'on' : ''}">Rooms</button><button data-color="evac" class="${S.color === 'evac' ? 'on' : ''}">Evacuation heat map</button></div>` +
    `<label class="fila"><input type="checkbox" id="fx" ${S.fx ? 'checked' : ''}> Effects (glow, floor points)</label>` +
    '<h2>Floors</h2>' +
    `<div class="ctl-fila"><span>Exploded view</span><span id="sep-v">${piesM(S.sep)}</span></div><input type="range" id="sep" min="0" max="${FX.explodeMax}" step="0.1" value="${S.sep}">` +
    `<div class="ctl-fila"><span>Context</span><span id="ctx-v">${Math.round(S.ctx * 100)} %</span></div><input type="range" id="ctx" min="0" max="100" step="5" value="${Math.round(S.ctx * 100)}">` +
    `<label class="fila"><input type="checkbox" id="ocultar" ${S.ocultarSup ? 'checked' : ''}> Hide upper floors</label>` +
    `<div class="hint">Floors stacked at their real level, derived from door heights; walls reach the next floor's slab (${piesM(FX.slabThick)}). Top floor wall height ${piesM(FLOOR_H)} — <span class="aviso">estimate</span>.${S.nivelEst.some(Boolean) ? ' <span class="aviso">Level of floor ' + S.nivelEst.map((x, i) => (x ? i + 1 : 0)).filter(Boolean).join(', ') + ' estimated</span> (no consistent door heights).' : ''} No roof.</div>` +
    '<h2>Layers</h2>' + capa('walls', 'Walls') + capa('doors', 'Doors') + capa('windows', 'Windows') + capa('slabs', 'Rooms') + capa('stairs', '3D stairs') + capa('labels', 'Room labels') + capa('route', 'Route of the selected room') + capa('sleeping', 'Sleeping areas') + capa('ignition', 'Ignition areas') + capa('incident', 'Incident layer') +
    '<h2>Room type filter</h2><div class="fila-btn"><button data-tipos="all">All</button><button data-tipos="stairs">Stairs</button><button data-tipos="bed">Bedrooms</button></div>' +
    ord.map((k) => `<label class="fila"><input type="checkbox" data-tipo="${esc(k)}" ${S.tipos.has(k) ? 'checked' : ''}> ${esc(k)} <span class="hint">(${tipos[k]})</span></label>`).join('') +
    '<div id="notas"></div>'
  notas()
}

// ---------------------------------------------------------------- seleccion y refresco
function seleccionar(ent, desdePadre = false) {
  if (HOUSE && ent && (ent.kind !== 'room' || (!desdePadre && S.sel && S.sel.id === ent.id && S.sel.fi === ent.fi))) ent = null // solo salas; segundo toque: quita la ruta
  S.sel = ent; S.salidaVista = null
  // la seleccion hecha por el usuario se comunica a la pagina que contiene el visor (no la que llega por mensaje)
  if (!desdePadre) avisarPadre({ type: 'select', id: ent ? ent.id : null, kind: ent ? ent.kind : null, floor: ent ? ent.fi + 1 : null })
  if (ent && ent.fi !== focoIdx()) { if (S.foco === 'all') S.f2d = ent.fi; else setFoco(ent.fi) }
  refrescar(); selectoresPlanta()
  const fl = document.querySelector('#tabla tr.sel'); if (fl) fl.scrollIntoView({ block: 'nearest' })
  if (HOUSE && ent && !rutaSala(ent.fi, salaPorId(ent.fi, ent.id))) avisoRuta('No exit route from this room')
}
let avisoT = 0
function avisoRuta(t) { // una linea, 2 s
  const el = $('aviso-ruta'); if (!el) return
  el.textContent = t; el.hidden = false; clearTimeout(avisoT); avisoT = setTimeout(() => { el.hidden = true }, 2000)
}
function refrescar() { estilo3D(); render2D(); tabla(); inspector(); notas(); barraSplit() }
function setDetalle(v) { S.detalle = v; if (v) construirDetalle(); panelIzq(); aplicarPlantas(); rotSucio = true; render2D(); avisoHud() }

function eventos() {
  $('izq').addEventListener('change', (e) => {
    const t = e.target
    if (t.dataset.capa) { S.capas[t.dataset.capa] = t.checked; if (t.dataset.capa === 'incident') dibujarIncidente3D() }
    else if (t.dataset.tipo) { t.checked ? S.tipos.add(t.dataset.tipo) : S.tipos.delete(t.dataset.tipo) }
    else if (t.id === 'heat') { S.color = t.checked ? 'evac' : 'type'; panelIzq(); refrescar(); return }
    else if (t.id === 'fx') { S.fx = t.checked; aplicarPlantas(); avisoHud(); return }
    else if (t.id === 'ocultar') { S.ocultarSup = t.checked; objetivos(); render2D(); return }
    else if (t.id === 'sep' || t.id === 'ctx') return
    refrescar()
  })
  $('izq').addEventListener('input', (e) => {
    if (e.target.id === 'ctx') { S.ctx = Number(e.target.value) / 100; $('ctx-v').textContent = Math.round(S.ctx * 100) + ' %'; objetivos(); render2D(); return }
    if (e.target.id !== 'sep') return
    S.sep = Number(e.target.value); $('sep-v').textContent = piesM(S.sep); objetivos()
  })
  $('izq').addEventListener('click', (e) => {
    const t = e.target
    if (t.dataset.color) { S.color = t.dataset.color; panelIzq(); refrescar() }
    if (t.dataset.modo) setDetalle(t.dataset.modo === 'detail')
    if (t.dataset.tipos) {
      const todos = [...document.querySelectorAll('#izq [data-tipo]')].map((x) => x.dataset.tipo)
      S.tipos = new Set(t.dataset.tipos === 'all' ? todos : t.dataset.tipos === 'stairs' ? todos.filter((k) => k === 'Staircase' || k === 'Stairs') : todos.filter((k) => k === 'Bedroom'))
      panelIzq(); refrescar()
    }
  })
  const clicPlanta = (e) => {
    const t = e.target.closest('button'); if (!t) return
    if (t.dataset.foco != null) setFoco(t.dataset.foco === 'all' ? 'all' : Number(t.dataset.foco))
    if (t.id === 'fit2d') { S.vb = null; render2D() }
  }
  $('plantas3d').addEventListener('click', clicPlanta)
  $('minipisos').addEventListener('click', clicPlanta)
  $('tabs2d').addEventListener('click', clicPlanta)
  window.addEventListener('keydown', (e) => {
    if (!S.plan || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) || e.metaKey || e.ctrlKey || e.altKey) return
    const n = S.plan.floors.length
    if (e.key === '0' || e.key === 'a' || e.key === 'A') setFoco('all')
    else if (/^[1-9]$/.test(e.key) && Number(e.key) <= n) setFoco(Number(e.key) - 1)
    else if (e.key === 'ArrowUp') { e.preventDefault(); setFoco(S.foco === 'all' ? 0 : Math.min(n - 1, S.foco + 1)) }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setFoco(S.foco === 'all' ? 'all' : S.foco === 0 ? 'all' : S.foco - 1) }
  })
  $('tabla').addEventListener('click', (e) => { const tr = e.target.closest('tr.fila'); if (tr) seleccionar({ kind: tr.dataset.kind, id: tr.dataset.id, fi: Number(tr.dataset.fi) }) })
  $('inspector').addEventListener('click', (e) => {
    if (e.target.id === 'cerrar-insp') return seleccionar(null)
    if (e.target.id === 'ver-salida' && S.sel && S.sel.kind === 'room') {
      const ruta = rutaSala(S.sel.fi, salaPorId(S.sel.fi, S.sel.id)), fin = ruta && ruta.puntos[ruta.puntos.length - 1]
      if (!fin) return
      S.capas.route = true; S.salidaVista = { id: fin.salida, fi: fin.fi }
      if (S.foco !== 'all') setFoco(fin.fi); else S.f2d = fin.fi
      refrescar(); selectoresPlanta()
    }
  })
  $('oblicuas-body').addEventListener('click', (e) => { const b = e.target.closest('[data-obl]'); if (b) { OBL.dir = b.dataset.obl; pintarOblicuas() } })
  $('modelo').addEventListener('change', () => { history.replaceState(null, '', '?sample=' + encodeURIComponent($('modelo').value)); cargar($('modelo').value) })
  window.addEventListener('message', mensaje)
}

// ---------------------------------------------------------------- mensajes de la pagina contenedora (mismo origen)
// {type:'focusFloor', floor: 'all' | 1..n}  {type:'highlight', id: <id de habitacion, puerta o ventana> | null}
// {type:'setLayer', layer: 'walls'|'doors'|'windows'|'slabs'|'stairs'|'labels'|'route'|'heatmap', on: true|false}
// {type:'loadSample', id: <id de samples/index.json>}: carga otra casa sin recargar la pagina
function mensaje(e) {
  if (e.origin !== ORIGEN || e.source !== window.parent || !e.data || typeof e.data !== 'object') return
  const d = e.data
  if (d.type === 'theme') return tema(d.vars)
  if (d.type === 'setIncident') return recibirIncidente(d.overlay)
  if (d.type === 'highlightRefs') { INC.resaltar = new Set(Array.isArray(d.refs) ? d.refs.filter((x) => typeof x === 'string').slice(0, 50) : []); return reconstruirIncidente() }
  if (d.type === 'loadSample') {
    if (!LISTA.some((x) => x.id === d.id)) return fallo('Unknown sample: ' + String(d.id).slice(0, 40))
    $('modelo').value = d.id
    return cargar(d.id)
  }
  if (!S.plan) return
  const n = S.plan.floors.length
  if (d.type === 'focusFloor') {
    if (d.floor === 'all') setFoco('all')
    else if (Number.isInteger(d.floor) && d.floor >= 1 && d.floor <= n) setFoco(d.floor - 1)
  } else if (d.type === 'highlight') {
    if (d.id == null) return seleccionar(null, true)
    for (let fi = 0; fi < n; fi++) {
      const f = piso(fi), r = f.rooms.find((x) => x.id === d.id && dibujable(x)), o = f.openings.find((x) => x.id === d.id && (x.type === 'doorway' || x.type === 'window'))
      if (r) return seleccionar({ kind: 'room', id: r.id, fi }, true)
      if (o) return seleccionar({ kind: o.type === 'window' ? 'window' : 'door', id: o.id, fi }, true)
    }
  } else if (d.type === 'setLayer' && typeof d.on === 'boolean') {
    if (d.layer === 'heatmap') { S.color = d.on ? 'evac' : 'type'; panelIzq(); refrescar() }
    else if (d.layer in S.capas) { S.capas[d.layer] = d.on; panelIzq(); refrescar() }
  }
}

// {type:'theme', vars:{'--color-bg': ..., '--font-mono': ...}}: tokens de diseno de la app (ver TOKENS en theme.js).
// Se aplica una vez por juego de tokens; se reconstruye el 3D y el 2D con los colores nuevos y se responde 'themeApplied'.
let temaClave = ''
function tema(vars) {
  const clave = JSON.stringify(vars || {})
  if (clave !== temaClave && aplicarTema(vars)) {
    temaClave = clave
    if (escena) { escena.background = fondoRadial(); escena.fog.color.set(T.bg0); if (bloom) rotSucio = true }
    if (S.plan) { const cam = camara.position.clone(), obj = controles.target.clone(); construir3D(); camara.position.copy(cam); controles.target.copy(obj); panelIzq(); selectoresPlanta(); refrescar(); avisoHud() }
  }
  avisarPadre({ type: 'themeApplied' })
}

// ---------------------------------------------------------------- capa de incidente: indice del plano y overlays (docs/INCIDENT_OVERLAY_SPEC.md)
// El indice del plano se genera al cargar (incident/plan-index.js, mismas reglas que scripts/make_plan_index.py) y se envia a
// la pagina contenedora. setIncident: si el plano aun no ha cargado se guarda el ultimo overlay y se aplica al cargar; las
// versiones menores que la actual se ignoran (respuesta incidentRejected); se valida con incident/validate.js (port de
// validate_overlay.py) contra el indice del plano cargado; si es valido se aplica y se guarda para el control de versiones.
const INC = { index: null, versions: new Map(), actual: null, pendiente: null, vista: null, rutas: null, resaltar: new Set(), escenarios: [], escenario: null, bannerAbierto: null, salas: new Set(), casa: null }
function prepararIncidente(plan) {
  INC.versions = new Map(); INC.actual = null; INC.vista = null; INC.rutas = null; INC.resaltar = new Set()
  try { INC.index = plan ? buildPlanIndex(plan) : emptyPlanIndex() } catch (e) { INC.index = null; avisarPadre({ type: 'planIndex', index: null, error: e.message }); return }
  INC.escenarios = DEBUG ? scenariosFor(S.id, S.modelId || S.id, plan, INC.index) : []
  reconstruirIncidente()
  // ?debug=1&scenario=S4&v=2: aplica ese escenario hasta esa version (las anteriores quedan en el control de versiones)
  const pedido = DEBUG && INC.escenarios.find((x) => x.key === Q.get('scenario'))
  if (pedido && !INC.pendiente) {
    const hasta = Math.max(1, Math.min(pedido.versions.length, Number(Q.get('v')) || 1))
    INC.escenario = { key: pedido.key, i: hasta - 1 }
    pedido.versions.slice(0, hasta).forEach((v) => aplicarIncidente(structuredClone(v)))
  }
  avisarPadre({ type: 'planIndex', index: INC.index })
  if (INC.pendiente) { const ov = INC.pendiente; INC.pendiente = null; aplicarIncidente(ov) }
}
function recibirIncidente(ov) {
  if (!S.plan || !INC.index) { INC.pendiente = ov; return } // plano aun no cargado: se queda el ultimo recibido
  aplicarIncidente(ov)
}
function aplicarIncidente(ov) {
  const version = ov && typeof ov === 'object' ? ov.version : undefined
  if (INC.actual && typeof version === 'number' && version < INC.actual.version)
    return avisarPadre({ type: 'incidentRejected', version, errors: [`version ${version} is older than the current version ${INC.actual.version} (ignored)`] })
  const errors = ov && typeof ov === 'object' && !Array.isArray(ov) ? validarOverlay(ov, INC.index) : ['overlay must be an object']
  if (errors.length) return avisarPadre({ type: 'incidentRejected', version, errors })
  if (INC.actual && ov.incident_id !== INC.actual.incident_id) { INC.versions = new Map(); INC.resaltar = new Set() } // otro incidente
  INC.actual = structuredClone(ov)
  INC.versions.set(version, INC.actual)
  INC.vista = version
  reconstruirIncidente()
  avisarPadre({ type: 'incidentApplied', version })
}

// ---------------------------------------------------------------- capa de incidente: dibujo (spec, "What the viewer draws")
// Colores saturados SOLO aqui (verde ocupantes, rojo fuego, ambar peligros), siempre con icono y texto. Nada se simula: ni
// propagacion ni personas no reportadas. Rutas del planificador (incident/planner.js), nunca del overlay.
const IC = { green: '#22c55e', red: '#ef4444', amber: '#f59e0b', smoke: '#a88780', route: '#ffffff', nearFire: '#ff8a80' }
const STALE_MIN = 10
const ovVista = () => (INC.vista != null && INC.versions.get(INC.vista)) || INC.actual
const fuente = (e) => [...new Set((e.evidence_ids || []).map((x) => String(x).split(':')[0]))].join(', ') || 'no evidence'
const minutos = (ov, e) => Math.max(0, Math.round((Date.parse(ov.as_of) - Date.parse(e.reported_at)) / 60000))
const caduco = (ov, e) => minutos(ov, e) > STALE_MIN
const nuevo = (ov, e) => { const prev = [...INC.versions.keys()].filter((v) => v < ov.version).sort((a, b) => b - a)[0]; if (prev === undefined) return false; const p = INC.versions.get(prev); return !['occupants', 'fire', 'hazards', 'blocked'].some((k) => (p[k] || []).some((x) => x.id === e.id && JSON.stringify(x) === JSON.stringify(e))) }
const nOcc = (o) => (o.count_range ? `${o.count_range[0]}-${o.count_range[1]}` : String(o.count))
const maxOcc = (o) => (o.count_range ? o.count_range[1] : o.count)
const salaDe = (fi, id) => fi === null ? null : piso(fi).rooms.find((r) => r.id === id) || null
const marcado = (e) => INC.resaltar.has(e.id) || (e.group_id && INC.resaltar.has(e.group_id))

/** Lugar dibujable de una entidad: { fi, room, p } (sala o ancla), { fi, floorOnly } (solo planta) o null (desconocido / fuera del escaneo). */
function lugar(e) {
  const loc = e.location
  if (!S.plan || !loc) return null
  const fi = floorOf(S.plan, loc)
  if (fi === null) return null
  if (loc.kind === 'floor') return { fi, floorOnly: true }
  const room = salaDe(fi, loc.kind === 'room' ? loc.room_id : loc.anchor.room_id)
  if (!room) return null
  const p = loc.kind === 'anchor' ? anchorPoint(S.plan, S.ev, loc) || centroSala(room) : centroSala(room)
  return p ? { fi, room, p, anchor: loc.kind === 'anchor' } : null
}

function reconstruirIncidente() {
  const ov = ovVista()
  // salas con personas o fuego reportados (layout=house: llevan su nombre)
  INC.salas = new Set(ov && S.plan ? [...(ov.occupants || []), ...(ov.fire || [])].map(lugar).filter((L) => L && L.room).map((L) => L.fi + ':' + L.room.id) : [])
  INC.rutas = ov && S.plan ? planRoutes(S.plan, ov, S.ev) : null
  if (INC.rutas) { // las rutas del planificador sustituyen a las del overlay y se validan como parte de el
    const errs = validarOverlay({ ...ov, routes: INC.rutas.routes }, INC.index)
    if (errs.length) { console.warn('[house-viewer] planner routes rejected by the validator', errs); INC.rutas = { ...INC.rutas, routes: [], geometry: new Map(), noRoute: (ov.objectives || []).map((o) => o.id) } }
  }
  casaIncidente(ov)
  if (S.plan) { dibujarIncidente3D(); render2D() }
  pintarPanelIncidente()
  avisarPadre({ type: 'incidentState', overlay: ov ? structuredClone(ov) : null, version: ov ? ov.version : null, versions: [...INC.versions.keys()],
    routes: INC.rutas ? INC.rutas.routes : [], noRoute: INC.rutas ? INC.rutas.noRoute : [], plannerMs: INC.rutas ? INC.rutas.ms : null, highlighted: [...INC.resaltar] })
}

// ---------------------------------------------------------------- 3D
let incG = [], incRutas = null
function texRadial(color, a0 = 0.85) {
  const c = document.createElement('canvas'); c.width = c.height = 256
  const g = c.getContext('2d'), gr = g.createRadialGradient(128, 128, 4, 128, 128, 128), col = new THREE.Color(color)
  const rgb = `${Math.round(col.r * 255)},${Math.round(col.g * 255)},${Math.round(col.b * 255)}`
  gr.addColorStop(0, `rgba(${rgb},${a0})`); gr.addColorStop(0.55, `rgba(${rgb},${a0 * 0.45})`); gr.addColorStop(1, `rgba(${rgb},0)`)
  g.fillStyle = gr; g.fillRect(0, 0, 256, 256)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t
}
function texRayas(color) {
  const c = document.createElement('canvas'); c.width = c.height = 64
  const g = c.getContext('2d'); g.strokeStyle = color; g.lineWidth = 10
  for (let i = -64; i < 128; i += 22) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i + 64, 64); g.stroke() }
  const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; return t
}
// rotulo con fondo, icono y color de la capa (legible: tamano minimo en pantalla)
function rotuloInc(lineas, color, op = 1) {
  const c = document.createElement('canvas'), g = c.getContext('2d'), px = 34, f = (w) => `${w} ${px}px ${T.font}`
  g.font = f(700); const w = Math.max(...lineas.map((l, i) => { g.font = f(i ? 500 : 700); return g.measureText(l).width })) + 36
  c.width = Math.ceil(w); c.height = 20 + lineas.length * (px + 10)
  g.fillStyle = 'rgba(8,10,14,0.82)'; g.fillRect(0, 0, c.width, c.height)
  g.fillStyle = color; g.fillRect(0, 0, 8, c.height)
  lineas.forEach((l, i) => { g.font = f(i ? 500 : 700); g.fillStyle = i ? '#e5e7eb' : color; g.textBaseline = 'top'; g.fillText(l, 22, 12 + i * (px + 10)) })
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.minFilter = THREE.LinearMipmapLinearFilter
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true, opacity: op, fog: false }))
  sp.renderOrder = 40; sp.center.set(0.5, 0)
  const h = 0.3 * lineas.length; legible(sp, h * c.width / c.height, h, 18 * lineas.length)
  return sp
}
const formaSala = (room, y) => { const g = new THREE.ShapeGeometry(new THREE.Shape(room.polygon.map((p) => new THREE.Vector2(p[0], p[1])))); g.rotateX(-Math.PI / 2); g.translate(0, y, 0); return g }
const matInc = (color, op, extra = {}) => new THREE.MeshBasicMaterial({ color: C(color), transparent: true, opacity: op, depthWrite: false, side: THREE.DoubleSide, fog: false, ...extra })
function contornoDiscontinuo(room, color, y, op) {
  const pts = [...room.polygon, room.polygon[0]].map((p) => m3(p[0], p[1], y))
  const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: C(color), dashSize: 0.3, gapSize: 0.2, transparent: true, opacity: op, depthTest: false, fog: false }))
  l.computeLineDistances(); l.renderOrder = 35; return l
}
function persona(color, op) {
  const g = new THREE.Group(), m = matInc(color, op, { depthTest: true })
  const cuerpo = new THREE.Mesh(new THREE.CylinderGeometry(0.11, 0.15, 0.95, 10), m); cuerpo.position.y = 0.48
  const cabeza = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 10), m); cabeza.position.y = 1.08
  g.add(cuerpo, cabeza); return g
}
function limpiarIncidente3D() {
  const tirar = (o) => o.traverse((x) => { if (x.geometry) x.geometry.dispose(); if (x.material) { if (x.material.map) x.material.map.dispose(); x.material.dispose() } })
  incG.forEach((o) => { o.parent?.remove(o); tirar(o) }); incG = []
  if (incRutas) { escena.remove(incRutas.grupo); tirar(incRutas.grupo); incRutas = null }
}
function dibujarIncidente3D() {
  if (!escena || !P.length) return
  limpiarIncidente3D()
  const ov = ovVista()
  if (!ov || !S.capas.incident) return
  if (HOUSE) return dibujarCasa3D(ov)
  // layout=house: ningun rotulo (todos los sprites de la capa son texto): personas, brillo rojo y rutas sin palabras
  const pon = (fi, o) => { if (HOUSE && o.isSprite) { o.material.map?.dispose(); o.material.dispose(); return } P[fi].g.add(o); incG.push(o) }
  const H = alturaTotal() + 1.5
  const tinte = (fi, color, op) => salasDe(piso(fi)).filter(poligonoValido).forEach((r) => pon(fi, new THREE.Mesh(formaSala(r, 0.04), matInc(color, op))))
  // ocupantes
  for (const o of ov.occupants || []) {
    const L = lugar(o); if (!L) continue
    const op = caduco(ov, o) ? 0.35 : 1, age = `${minutos(ov, o)} min ago${caduco(ov, o) ? ' · stale' : ''}`, tag = nuevo(ov, o) ? ' · NEW' : ''
    const contra = o.status === 'contradicted'
    if (L.floorOnly) {
      if (contra) salasDe(piso(L.fi)).filter(poligonoValido).forEach((r) => pon(L.fi, contornoDiscontinuo(r, IC.green, 0.06, 0.7 * op)))
      else tinte(L.fi, IC.green, 0.1 * op)
      const b = limitesPiso(piso(L.fi)), sp = rotuloInc([`${contra ? '? ' : ''}${nOcc(o)} reported · floor ${L.fi + 1}${tag}`, `room unknown · ${age} · ${fuente(o)}`], IC.green, op)
      sp.position.copy(m3((b.minX + b.maxX) / 2, b.maxY + 0.4, 1.2)); pon(L.fi, sp)
      continue
    }
    if (contra) {
      pon(L.fi, contornoDiscontinuo(L.room, IC.green, 0.06, 0.95 * op))
      const q = rotuloInc([`? ${nOcc(o)} reported (contradicted)${tag}`, `${age} · ${fuente(o)}`], IC.green, op); q.position.copy(m3(L.p[0], L.p[1], 1.5)); pon(L.fi, q)
    } else {
      const n = Math.min(5, maxOcc(o))
      for (let i = 0; i < n; i++) { const a = (i / Math.max(1, n)) * Math.PI * 2, r = n > 1 ? 0.35 : 0, f = persona(IC.green, 0.9 * op); f.position.copy(m3(L.p[0] + Math.cos(a) * r, L.p[1] + Math.sin(a) * r, 0)); pon(L.fi, f) }
      const glow = new THREE.Mesh(new THREE.CircleGeometry(1.1, 40), new THREE.MeshBasicMaterial({ map: texRadial(IC.green, 0.8), transparent: true, opacity: op, depthWrite: false, fog: false }))
      glow.rotation.x = -Math.PI / 2; glow.position.copy(m3(L.p[0], L.p[1], 0.03)); pon(L.fi, glow)
      const lbl = rotuloInc([`${nOcc(o)}${maxOcc(o) > 5 ? ` (x${maxOcc(o)})` : ''} reported${tag}`, `${age} · ${fuente(o)}`], IC.green, op); lbl.position.copy(m3(L.p[0], L.p[1], 1.45)); pon(L.fi, lbl)
    }
    const baliza = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, H - plantaY(L.fi), 8), matInc(IC.green, 0.55 * op, { depthTest: false }))
    baliza.position.copy(m3(L.p[0], L.p[1], (H - plantaY(L.fi)) / 2)); baliza.renderOrder = 34; pon(L.fi, baliza)
    if (marcado(o)) pon(L.fi, contornoDiscontinuo(L.room, '#ffffff', 0.08, 1))
  }
  // fuego y humo
  for (const f of ov.fire || []) {
    const L = lugar(f); if (!L) continue
    const op = caduco(ov, f) ? 0.4 : 1, age = `${minutos(ov, f)} min ago${caduco(ov, f) ? ' · stale' : ''}`, tag = nuevo(ov, f) ? ' · NEW' : ''
    const nombre = `${f.kind.toUpperCase()} (${f.status})`
    if (L.floorOnly) {
      tinte(L.fi, IC.red, 0.08 * op)
      const b = limitesPiso(piso(L.fi)), sp = rotuloInc([`${nombre} · floor ${L.fi + 1}${tag}`, `room unknown · ${age} · ${fuente(f)}`], IC.red, op)
      sp.position.copy(m3((b.minX + b.maxX) / 2, b.minY - 0.4, 1.2)); pon(L.fi, sp); continue
    }
    if (f.kind === 'smoke') {
      [1.4, 2.0].forEach((y, i) => pon(L.fi, new THREE.Mesh(formaSala(L.room, y), matInc(IC.smoke, (0.28 - i * 0.08) * op))))
    } else {
      const R = L.anchor ? 1.6 : Math.max(1.4, Math.sqrt((L.room.areaM2 || 9)) * 0.7)
      const geo = formaSala(L.room, 0.05), uv = geo.attributes.uv
      for (let i = 0; i < uv.count; i++) uv.setXY(i, (uv.getX(i) - L.p[0]) / (2 * R) + 0.5, (uv.getY(i) - L.p[1]) / (2 * R) + 0.5)
      const tx = texRadial(IC.red, 0.9); tx.wrapS = tx.wrapT = THREE.ClampToEdgeWrapping
      pon(L.fi, new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tx, transparent: true, opacity: op, depthWrite: false, side: THREE.DoubleSide, fog: false })))
      const col = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.7, 2.3, 20, 1, true), matInc(IC.red, 0.16 * op, { blending: THREE.AdditiveBlending }))
      col.position.copy(m3(L.p[0], L.p[1], 1.15)); pon(L.fi, col)
    }
    const lbl = rotuloInc([`${f.kind === 'smoke' ? '≋' : '▲'} ${nombre}${tag}`, `${L.anchor ? f.location.anchor.position.replace('_', ' ') + ' · ' : ''}${age} · ${fuente(f)}`], f.kind === 'smoke' ? IC.smoke : IC.red, op)
    lbl.position.copy(m3(L.p[0], L.p[1], 2.4)); pon(L.fi, lbl)
    if (marcado(f)) pon(L.fi, contornoDiscontinuo(L.room, '#ffffff', 0.08, 1))
  }
  // peligros
  for (const hz of ov.hazards || []) {
    const L = lugar(hz); if (!L) continue
    const op = caduco(ov, hz) ? 0.4 : 1, b = L.floorOnly ? limitesPiso(piso(L.fi)) : null
    const sp = rotuloInc([`⚠ ${hz.kind.toUpperCase()} (${hz.status})${nuevo(ov, hz) ? ' · NEW' : ''}`, `${L.floorOnly ? `floor ${L.fi + 1}, room unknown · ` : ''}conf. ${Math.round(hz.confidence * 100)}% · ${minutos(ov, hz)} min ago${caduco(ov, hz) ? ' · stale' : ''} · ${fuente(hz)}`], IC.amber, op)
    sp.position.copy(L.floorOnly ? m3(b.maxX, (b.minY + b.maxY) / 2, 1.2) : m3(L.p[0], L.p[1], 1.0)); pon(L.fi, sp)
    if (!L.floorOnly) { const d = new THREE.Mesh(new THREE.CircleGeometry(0.5, 4), matInc(IC.amber, 0.5 * op)); d.rotation.x = -Math.PI / 2; d.position.copy(m3(L.p[0], L.p[1], 0.05)); pon(L.fi, d) }
    if (marcado(hz) && L.room) pon(L.fi, contornoDiscontinuo(L.room, '#ffffff', 0.08, 1))
  }
  // bloqueos: puerta o escalera rayada
  for (const bl of ov.blocked || []) {
    const ref = bl.ref_id
    S.plan.floors.forEach((f, fi) => {
      const o = f.openings.find((x) => x.id === ref)
      if (o) { const pl = new THREE.Mesh(new THREE.PlaneGeometry(Math.max(0.6, o.width), 2.1), new THREE.MeshBasicMaterial({ map: texRayas(IC.amber), transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false, fog: false }))
        pl.position.copy(m3(o.center[0], o.center[1], 1.05)); pl.rotation.y = (o.angleDeg * Math.PI) / 180; pon(fi, pl) }
      const st = (S.plan.stairs || []).find((x) => x.id === ref)
      const rooms = st ? [st.lower_room_id, st.upper_room_id] : [ref]
      for (const r of f.rooms.filter((x) => rooms.includes(x.id) && poligonoValido(x))) {
        const tx = texRayas(IC.amber); tx.repeat.set(1.5, 1.5)
        pon(fi, new THREE.Mesh(formaSala(r, 0.07), new THREE.MeshBasicMaterial({ map: tx, transparent: true, opacity: 0.8, depthWrite: false, side: THREE.DoubleSide, fog: false })))
      }
    })
    const L = (() => { for (const [fi, f] of S.plan.floors.entries()) { const o = f.openings.find((x) => x.id === ref); if (o) return { fi, p: o.center }; const r = f.rooms.find((x) => x.id === ref || (S.plan.stairs || []).some((s) => s.id === ref && (s.lower_room_id === x.id))); if (r && poligonoValido(r)) return { fi, p: centroSala(r) } } return null })()
    if (L) { const sp = rotuloInc([`⛔ BLOCKED (${bl.status})`, `${minutos(ov, bl)} min ago · ${fuente(bl)}`], IC.amber); sp.position.copy(m3(L.p[0], L.p[1], 2.2)); pon(L.fi, sp) }
  }
  // rutas del planificador: tramos por planta (grupo de la planta), conectores de escalera y cuentas animadas (escena)
  const grupo = new THREE.Group(); escena.add(grupo)
  incRutas = { grupo, rutas: [] }
  if (INC.rutas) for (const [id, g] of INC.rutas.geometry) {
    const r = INC.rutas.routes.find((x) => x.id === id), dash = g.dashed
    const pts = g.pts, segs = [], cuentas = []
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i], color = b.near ? IC.nearFire : IC.route
      if (a.fi === b.fi) {
        const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([m3(a.p[0], a.p[1], 0.35), m3(b.p[0], b.p[1], 0.35)]),
          new THREE.LineDashedMaterial({ color: C(color), dashSize: dash ? 0.18 : 0.35, gapSize: dash ? 0.28 : 0.14, transparent: true, opacity: dash ? 0.7 : 0.95, depthTest: false, fog: false }))
        l.computeLineDistances(); l.renderOrder = 36; pon(a.fi, l)
      } else {
        const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]), new THREE.LineBasicMaterial({ color: C(color), transparent: true, opacity: 0.8, depthTest: false, fog: false }))
        l.renderOrder = 36; grupo.add(l); segs.push({ l, a, b })
      }
    }
    pts.forEach((q, i) => { const sp = rotuloInc([String(i + 1)], b2(q.near), 1); sp.scale.multiplyScalar(0.6); legible(sp, sp.scale.x, sp.scale.y, 14); sp.position.copy(m3(q.p[0], q.p[1], 0.6)); pon(q.fi, sp) })
    const fin = pts[pts.length - 1], lbl = rotuloInc([`${r.kind.toUpperCase()} ROUTE · ${r.objective_id}${dash ? ' (candidate)' : ''}`, `~${piesM(r.length_m_estimate)} estimate${r.passes_near_fire ? ' · passes near fire' : ''}`], r.passes_near_fire ? IC.nearFire : IC.route, dash ? 0.75 : 1)
    lbl.position.copy(m3(fin.p[0], fin.p[1], 1.9)); pon(fin.fi, lbl)
    for (let k = 0; k < 3; k++) { const m = new THREE.Mesh(new THREE.SphereGeometry(0.07, 10, 8), new THREE.MeshBasicMaterial({ color: C(IC.route), depthTest: false, transparent: true, fog: false })); m.renderOrder = 37; grupo.add(m); cuentas.push(m) }
    incRutas.rutas.push({ pts, segs, cuentas })
  }
  function b2(near) { return near ? IC.nearFire : IC.route }
  rotSucio = true
}
// cada fotograma: conectores entre plantas (siguen la vista explosionada) y cuentas que recorren la ruta
function animarIncidente() {
  if (!incRutas || !incRutas.rutas.length) return
  const w = (q, h = 0.35) => { const v = m3(q.p[0], q.p[1], h); v.y += P[q.fi] ? P[q.fi].cur.y : plantaY(q.fi); return v }
  const t = (performance.now() / 4000) % 1
  for (const R of incRutas.rutas) {
    R.segs.forEach(({ l, a, b }) => { l.geometry.setFromPoints([w(a), w(b)]) })
    const P3 = R.pts.map((q) => w(q)), L = P3.slice(1).map((p, i) => p.distanceTo(P3[i])), tot = L.reduce((x, y) => x + y, 0) || 1
    R.cuentas.forEach((m, k) => {
      let d = (((t + k / R.cuentas.length) % 1) * tot), i = 0
      while (i < L.length - 1 && d > L[i]) { d -= L[i]; i++ }
      m.position.copy(P3[i]).lerp(P3[i + 1] || P3[i], L[i] ? d / L[i] : 0)
    })
  }
}

// ---------------------------------------------------------------- 2D (planta del plano 2D)
function svgIncidente2D(fi, ns) {
  const ov = ovVista()
  if (!ov || !S.capas.incident) return ''
  if (HOUSE) return svgCasa2D(fi, ns, ov)
  const fs = Math.max(0.16, 12 * Math.max(S.vb[2] / ($('vista2d').clientWidth || 400), S.vb[3] / ($('vista2d').clientHeight || 300)))
  let h = `<defs><radialGradient id="incFuego"><stop offset="0" stop-color="${IC.red}" stop-opacity="0.85"/><stop offset="0.6" stop-color="${IC.red}" stop-opacity="0.35"/><stop offset="1" stop-color="${IC.red}" stop-opacity="0"/></radialGradient>` +
    `<radialGradient id="incVerde"><stop offset="0" stop-color="${IC.green}" stop-opacity="0.7"/><stop offset="1" stop-color="${IC.green}" stop-opacity="0"/></radialGradient>` +
    `<pattern id="incRayas" patternUnits="userSpaceOnUse" width="0.35" height="0.35" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="0.35" stroke="${IC.amber}" stroke-width="0.14"/></pattern></defs>`
  const txt = (x, y, lines, color, op) => { if (HOUSE) return ''; const w = Math.max(...lines.map((l) => l.length)) * fs * 0.62 + fs * 0.8, hh = lines.length * fs * 1.25 + fs * 0.4
    return `<g opacity="${op}" pointer-events="none"><rect x="${x - w / 2}" y="${y - hh}" width="${w}" height="${hh}" fill="#080a0e" fill-opacity="0.82"/><rect x="${x - w / 2}" y="${y - hh}" width="${fs * 0.22}" height="${hh}" fill="${color}"/>` +
      lines.map((l, i) => `<text x="${x - w / 2 + fs * 0.5}" y="${y - hh + fs * 1.05 + i * fs * 1.25}" font-size="${fs}" font-family="${T.font}" font-weight="${i ? 500 : 700}" fill="${i ? '#e5e7eb' : color}">${esc(l)}</text>`).join('') + '</g>' }
  const anillo2 = (r) => anillo(r.polygon)
  const salas = salasDe(piso(fi)).filter(poligonoValido)
  for (const o of ov.occupants || []) {
    const L = lugar(o); if (!L || L.fi !== fi) continue
    const op = caduco(ov, o) ? 0.4 : 1, contra = o.status === 'contradicted', age = `${minutos(ov, o)} min ago${caduco(ov, o) ? ' · stale' : ''}`
    if (L.floorOnly) {
      h += salas.map((r) => `<path d="${anillo2(r)}" fill="${contra ? 'none' : IC.green}" fill-opacity="0.12" stroke="${IC.green}" stroke-opacity="${contra ? 0.8 : 0.35}" stroke-width="1.2" ${contra ? 'stroke-dasharray="5 4"' : ''} ${ns} opacity="${op}"/>`).join('')
      const b = limitesPiso(piso(fi)); h += txt((b.minX + b.maxX) / 2, -b.maxY - fs * 0.4, [`${contra ? '? ' : ''}${nOcc(o)} reported · room unknown`, `${age} · ${fuente(o)}`], IC.green, op); continue
    }
    if (contra) { h += `<path d="${anillo2(L.room)}" fill="none" stroke="${IC.green}" stroke-width="2" stroke-dasharray="6 4" ${ns} opacity="${op}"/>`; h += txt(L.p[0], -L.p[1] - fs * 0.3, [`? ${nOcc(o)} reported (contradicted)`, `${age} · ${fuente(o)}`], IC.green, op) }
    else {
      h += `<circle cx="${L.p[0]}" cy="${-L.p[1]}" r="1.1" fill="url(#incVerde)" opacity="${op}"/>`
      const n = Math.min(5, maxOcc(o))
      for (let i = 0; i < n; i++) { const a = (i / Math.max(1, n)) * Math.PI * 2, rr = n > 1 ? 0.35 : 0, x = L.p[0] + Math.cos(a) * rr, y = -L.p[1] - Math.sin(a) * rr
        h += `<g opacity="${op}"><circle cx="${x}" cy="${y - 0.16}" r="0.1" fill="${IC.green}"/><path d="M${x - 0.13} ${y + 0.2} Q${x} ${y - 0.12} ${x + 0.13} ${y + 0.2} Z" fill="${IC.green}"/></g>` }
      h += txt(L.p[0], -L.p[1] - 0.55, [`${nOcc(o)}${maxOcc(o) > 5 ? ` (x${maxOcc(o)})` : ''} reported`, `${age} · ${fuente(o)}`], IC.green, op)
    }
    if (marcado(o)) h += `<path d="${anillo2(L.room)}" fill="none" stroke="#fff" stroke-width="3" ${ns} filter="url(#brillo)"/>`
  }
  for (const f of ov.fire || []) {
    const L = lugar(f); if (!L || L.fi !== fi) continue
    const op = caduco(ov, f) ? 0.45 : 1, nombre = `${f.kind.toUpperCase()} (${f.status})`, age = `${minutos(ov, f)} min ago${caduco(ov, f) ? ' · stale' : ''}`
    if (L.floorOnly) { h += salas.map((r) => `<path d="${anillo2(r)}" fill="${IC.red}" fill-opacity="0.1" stroke="${IC.red}" stroke-opacity="0.35" stroke-width="1" ${ns} opacity="${op}"/>`).join(''); const b = limitesPiso(piso(fi)); h += txt((b.minX + b.maxX) / 2, -b.minY + fs * 2.6, [`${nombre} · room unknown`, `${age} · ${fuente(f)}`], IC.red, op); continue }
    const clip = `incClip-${f.id.replace(/[^a-z0-9-]/gi, '')}`
    h += `<clipPath id="${clip}"><path d="${anillo2(L.room)}"/></clipPath>`
    if (f.kind === 'smoke') h += `<path d="${anillo2(L.room)}" fill="${IC.smoke}" fill-opacity="${0.45 * op}"/>`
    else { const R = L.anchor ? 1.6 : Math.max(1.4, Math.sqrt(L.room.areaM2 || 9) * 0.7); h += `<circle cx="${L.p[0]}" cy="${-L.p[1]}" r="${R}" fill="url(#incFuego)" clip-path="url(#${clip})" opacity="${op}"/>` }
    h += txt(L.p[0], -L.p[1] + (f.kind === 'smoke' ? fs * 2.6 : -0.2), [`${f.kind === 'smoke' ? '≋' : '▲'} ${nombre}`, `${L.anchor ? f.location.anchor.position.replace('_', ' ') + ' · ' : ''}${age} · ${fuente(f)}`], f.kind === 'smoke' ? IC.smoke : IC.red, op)
    if (marcado(f)) h += `<path d="${anillo2(L.room)}" fill="none" stroke="#fff" stroke-width="3" ${ns} filter="url(#brillo)"/>`
  }
  for (const hz of ov.hazards || []) {
    const L = lugar(hz); if (!L || L.fi !== fi || L.floorOnly) continue
    const op = caduco(ov, hz) ? 0.45 : 1
    h += `<path d="M${L.p[0]} ${-L.p[1] - 0.32} L${L.p[0] + 0.3} ${-L.p[1] + 0.2} L${L.p[0] - 0.3} ${-L.p[1] + 0.2} Z" fill="${IC.amber}" opacity="${op}"/>` + txt(L.p[0], -L.p[1] - 0.45, [`⚠ ${hz.kind.toUpperCase()} (${hz.status})`, `conf. ${Math.round(hz.confidence * 100)}% · ${fuente(hz)}`], IC.amber, op)
    if (marcado(hz)) h += `<path d="${anillo2(L.room)}" fill="none" stroke="#fff" stroke-width="3" ${ns} filter="url(#brillo)"/>`
  }
  for (const bl of ov.blocked || []) {
    const o = piso(fi).openings.find((x) => x.id === bl.ref_id), st = (S.plan.stairs || []).find((x) => x.id === bl.ref_id)
    if (o) { const a = (o.angleDeg * Math.PI) / 180, hw = Math.max(0.3, o.width / 2); h += `<rect x="${o.center[0] - hw}" y="${-o.center[1] - 0.25}" width="${hw * 2}" height="0.5" fill="url(#incRayas)" stroke="${IC.amber}" stroke-width="1" ${ns} transform="rotate(${-o.angleDeg} ${o.center[0]} ${-o.center[1]})"/>` }
    for (const r of salas.filter((x) => x.id === bl.ref_id || (st && (x.id === st.lower_room_id || x.id === st.upper_room_id)))) h += `<path d="${anillo2(r)}" fill="url(#incRayas)" stroke="${IC.amber}" stroke-width="1.5" ${ns}/>`
  }
  if (INC.rutas) for (const [id, g] of INC.rutas.geometry) {
    const r = INC.rutas.routes.find((x) => x.id === id), pts = g.pts
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i]
      if (a.fi !== fi || b.fi !== fi) continue
      h += `<line x1="${a.p[0]}" y1="${-a.p[1]}" x2="${b.p[0]}" y2="${-b.p[1]}" stroke="${b.near ? IC.nearFire : IC.route}" stroke-width="2.4" stroke-dasharray="${g.dashed ? '3 5' : '7 4'}" stroke-linecap="round" opacity="${g.dashed ? 0.75 : 1}" ${ns}><animate attributeName="stroke-dashoffset" from="22" to="0" dur="1s" repeatCount="indefinite"/></line>`
    }
    pts.forEach((q, i) => { if (q.fi === fi && !HOUSE) h += `<circle cx="${q.p[0]}" cy="${-q.p[1]}" r="${fs * 0.75}" fill="#080a0e" stroke="${q.near ? IC.nearFire : IC.route}" stroke-width="1.2" ${ns}/><text x="${q.p[0]}" y="${-q.p[1] + fs * 0.35}" font-size="${fs}" font-family="${T.font}" fill="#fff" text-anchor="middle" pointer-events="none">${i + 1}</text>` })
    const fin = pts[pts.length - 1]
    if (fin.fi === fi) h += txt(fin.p[0], -fin.p[1] - fs * 1.1, [`${r.kind.toUpperCase()} · ${r.objective_id}${g.dashed ? ' (candidate)' : ''}`, `~${piesM(r.length_m_estimate)} estimate${r.passes_near_fire ? ' · near fire' : ''}`], r.passes_near_fire ? IC.nearFire : IC.route, g.dashed ? 0.8 : 1)
  }
  return `<g class="incidente">${h}</g>`
}

// ---------------------------------------------------------------- layout=house: personas, brillo de fuego y rutas roja / verde
// Sin texto ni cifras. Personas: verdes (una sala, sin contradiccion) y amarillas (candidatas de un grupo contradicho y
// personas solo con planta). Fuego: degradado radial que decae con la distancia, recortado al contorno de la casa (union de
// las salas con contorno fiable). Rutas (incident/house-routes.js): roja de la entrada al fuego, verde de la entrada por todas
// las personas hasta una salida. Atributos ocultos en <body> para las pruebas: data-people-green / -yellow (figuras),
// data-route-red / -green (rutas dibujadas) y data-unreachable (paradas sin ruta).
const YELLOW = '#facc15'
function casaIncidente(ov) {
  if (!HOUSE) return
  INC.casa = null
  if (ov && S.plan && S.ev) {
    try { const people = housePeople(S.plan, S.ev, ov); INC.casa = { people, ...houseRoutes(S.plan, S.ev, ov, people) } }
    catch (e) { console.warn('[house-viewer] house routes failed', e) }
  }
  const c = INC.casa, n = (col) => (c ? c.people.filter((x) => x.color === col).reduce((a, x) => a + x.n, 0) : 0)
  Object.assign(document.body.dataset, { peopleGreen: n('green'), peopleYellow: n('yellow'), routeRed: c && c.red ? 1 : 0, routeGreen: c && c.green ? 1 : 0, unreachable: c ? c.unreachable : 0 })
}
// fuegos dibujables (con sala) y radio del degradado: min(8 m, 35 % de la mayor dimension de la planta)
function fuegosCasa(ov) {
  return (ov.fire || []).map((f) => {
    const L = lugar(f); if (!L || !L.room) return null
    const b = limitesPiso(piso(L.fi)), R = Math.min(8, 0.35 * Math.max(b.maxX - b.minX, b.maxY - b.minY))
    return { f, L, R, k: (f.kind === 'smoke' ? 0.6 : 1) * (caduco(ov, f) ? 0.5 : 1) }
  }).filter(Boolean)
}
const FUEGO_STOPS = [[0, 0.9], [0.3, 0.6], [0.6, 0.28], [1, 0]] // opacidad frente a la distancia relativa al radio
function texFuego() {
  const c = document.createElement('canvas'); c.width = c.height = 256
  const g = c.getContext('2d'), gr = g.createRadialGradient(128, 128, 0, 128, 128, 128)
  FUEGO_STOPS.forEach(([o, a]) => gr.addColorStop(o, `rgba(239,68,68,${a})`))
  g.fillStyle = gr; g.fillRect(0, 0, 256, 256)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping; return t
}
const colorPersona = (x) => (x.color === 'green' ? IC.green : YELLOW)
const posFiguras = (x) => Array.from({ length: x.n }, (_, i) => { const a = (i / x.n) * Math.PI * 2 + Math.PI / 2, r = x.n > 1 ? 0.38 : 0; return [x.p[0] + Math.cos(a) * r, x.p[1] + Math.sin(a) * r] })
function dibujarCasa3D(ov) {
  const pon = (fi, o) => { P[fi].g.add(o); incG.push(o) }
  for (const { L, R, k } of fuegosCasa(ov)) {
    const tx = texFuego()
    for (const r of salasDe(piso(L.fi)).filter(poligonoValido)) { // solo dentro de las salas: no sale del contorno
      const geo = formaSala(r, 0.045), uv = geo.attributes.uv
      for (let i = 0; i < uv.count; i++) uv.setXY(i, (uv.getX(i) - L.p[0]) / (2 * R) + 0.5, (uv.getY(i) - L.p[1]) / (2 * R) + 0.5)
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tx, transparent: true, opacity: k, depthWrite: false, side: THREE.DoubleSide, fog: false }))
      m.userData.fuego = true; pon(L.fi, m)
    }
    const sala = new THREE.Mesh(formaSala(L.room, 0.05), matInc(IC.red, 0.38 * k)); sala.userData.fuego = true; pon(L.fi, sala) // maximo en la sala del fuego
  }
  const c = INC.casa
  if (c) for (const x of c.people) for (const q of posFiguras(x)) { const f = persona(colorPersona(x), 0.95); f.position.copy(m3(q[0], q[1], 0)); f.userData.persona = x.color; pon(x.fi, f) }
  const grupo = new THREE.Group(); escena.add(grupo)
  incRutas = { grupo, rutas: [] }
  const ruta3D = (pts, color, h) => {
    const segs = [], cuentas = [], mat = () => new THREE.MeshBasicMaterial({ color: C(color), depthTest: false, transparent: true, opacity: 0.95, fog: false })
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i]
      if (a.fi === b.fi) {
        const A = m3(a.p[0], a.p[1], h), B = m3(b.p[0], b.p[1], h), len = A.distanceTo(B); if (len < 1e-4) continue
        const cil = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, len, 8), mat())
        cil.position.copy(A).add(B).multiplyScalar(0.5); cil.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), B.clone().sub(A).normalize()); cil.renderOrder = 36
        cil.userData.ruta = color; pon(a.fi, cil)
      } else {
        const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]), new THREE.LineBasicMaterial({ color: C(color), transparent: true, opacity: 0.9, depthTest: false, fog: false }))
        l.renderOrder = 36; grupo.add(l); segs.push({ l, a, b })
      }
    }
    for (let k = 0; k < 3; k++) { const m = new THREE.Mesh(new THREE.SphereGeometry(0.09, 10, 8), mat()); m.renderOrder = 37; grupo.add(m); cuentas.push(m) }
    incRutas.rutas.push({ pts, segs, cuentas })
  }
  if (c && c.red) ruta3D(c.red.pts, IC.red, 0.3)
  if (c && c.green) ruta3D(c.green.pts, IC.green, 0.42)
  rotSucio = true
}
function svgCasa2D(fi, ns, ov) {
  let defs = '', h = ''
  const fuegos = fuegosCasa(ov).filter((x) => x.L.fi === fi)
  if (fuegos.length) defs += `<clipPath id="casaContorno">${salasDe(piso(fi)).filter(poligonoValido).map((r) => `<path d="${anillo(r.polygon)}"/>`).join('')}</clipPath>`
  fuegos.forEach(({ L, R, k }, i) => {
    defs += `<radialGradient id="casaFuego${i}" gradientUnits="userSpaceOnUse" cx="${L.p[0]}" cy="${-L.p[1]}" r="${R}">` + FUEGO_STOPS.map(([o, a]) => `<stop offset="${o}" stop-color="${IC.red}" stop-opacity="${a * k}"/>`).join('') + '</radialGradient>'
    h += `<g clip-path="url(#casaContorno)" pointer-events="none" data-fire-glow><circle cx="${L.p[0]}" cy="${-L.p[1]}" r="${R}" fill="url(#casaFuego${i})"/>` +
      `<path d="${anillo(L.room.polygon)}" fill="${IC.red}" fill-opacity="${0.38 * k}"/></g>`
  })
  const c = INC.casa
  const linea = (pts, color, tipo) => {
    let d = ''
    for (let i = 1; i < pts.length; i++) { const a = pts[i - 1], b = pts[i]; if (a.fi === fi && b.fi === fi) d += `M${a.p[0]} ${-a.p[1]}L${b.p[0]} ${-b.p[1]}` }
    return `<g data-route="${tipo}" pointer-events="none">${d ? `<path d="${d}" fill="none" stroke="#080a0e" stroke-opacity="0.55" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round" ${ns}/><path d="${d}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" ${ns}/>` : ''}</g>`
  }
  if (c && c.red) h += linea(c.red.pts, IC.red, 'red')
  if (c && c.green) h += linea(c.green.pts, IC.green, 'green')
  if (c) for (const x of c.people) if (x.fi === fi) for (const [px, py] of posFiguras(x)) {
    const y = -py, col = colorPersona(x)
    h += `<g data-person="${x.color}" pointer-events="none"><circle cx="${px}" cy="${y - 0.2}" r="0.13" fill="${col}" stroke="#080a0e" stroke-width="0.8" ${ns}/><path d="M${px - 0.17} ${y + 0.24} Q${px} ${y - 0.16} ${px + 0.17} ${y + 0.24} Z" fill="${col}" stroke="#080a0e" stroke-width="0.8" ${ns}/></g>`
  }
  return `<g class="incidente">${defs ? `<defs>${defs}</defs>` : ''}${h}</g>`
}

// ---------------------------------------------------------------- banner, versiones, consideraciones y escenarios (DEBUG)
function asegurarDomIncidente() {
  if ($('inc-banner')) return
  const ban = document.createElement('div'); ban.id = 'inc-banner'; ban.className = 'hud'; $('centro').appendChild(ban)
  const det = document.createElement('details'); det.id = 'inc-panel'; det.className = 'plegable'; det.open = true
  if (HOST) det.style.display = 'none' // la UI anfitriona muestra un unico panel de consideraciones
  det.innerHTML = '<summary>Considerations · incident</summary><div id="inc-panel-body"></div>'
  const der = $('der'), ref = $('oblicuas'); der.insertBefore(det, ref || null)
  if (DEBUG) { const d = document.createElement('div'); d.id = 'inc-debug'; document.body.appendChild(d) }
  ban.addEventListener('click', (e) => {
    if (e.target.closest('[data-inctog]')) { INC.bannerAbierto = ban.classList.contains('plegado'); pintarPanelIncidente(); return }
    const b = e.target.closest('[data-incv]'); if (b) { INC.vista = Number(b.dataset.incv); reconstruirIncidente() } })
  det.addEventListener('click', (e) => { const c = e.target.closest('[data-con]'); if (!c) return
    const ov = ovVista(), con = (ov.considerations || []).find((x) => x.id === c.dataset.con); if (!con) return
    const igual = con.refs.length && con.refs.every((r) => INC.resaltar.has(r)) && INC.resaltar.size === con.refs.length
    INC.resaltar = igual ? new Set() : new Set(con.refs); reconstruirIncidente() })
  if (DEBUG) $('inc-debug').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return
    if (b.dataset.tog) { const d = $('inc-debug'); d.classList.toggle('abierto'); b.setAttribute('aria-expanded', d.classList.contains('abierto')); return }
    if (b.dataset.esc) { const sc = INC.escenarios.find((x) => x.key === b.dataset.esc); INC.escenario = { key: sc.key, i: 0 }; INC.versions = new Map(); INC.actual = null; aplicarIncidente(structuredClone(sc.versions[0])) }
    if (b.dataset.next) { const sc = INC.escenarios.find((x) => x.key === INC.escenario.key); INC.escenario.i = Math.min(sc.versions.length - 1, INC.escenario.i + 1); aplicarIncidente(structuredClone(sc.versions[INC.escenario.i])) }
    if (b.dataset.clear) { INC.actual = null; INC.versions = new Map(); INC.vista = null; INC.escenario = null; reconstruirIncidente() }
    pintarPanelIncidente()
  })
}
const AVISO = 'Estimate from caller reports and a partial scan. Advisory only.'
function pintarPanelIncidente() {
  asegurarDomIncidente()
  const ov = ovVista(), ban = $('inc-banner'), body = $('inc-panel-body')
  if (DEBUG) {
    const sc = INC.escenario && INC.escenarios.find((x) => x.key === INC.escenario.key)
    $('inc-debug').innerHTML = `<button class="inc-chip" data-tog="1" aria-expanded="${$('inc-debug').classList.contains('abierto')}">DEBUG</button>` + INC.escenarios.map((x) => `<button data-esc="${x.key}" class="${INC.escenario?.key === x.key ? 'on' : ''}">${esc(x.label)}</button>`).join('') +
      (sc && sc.versions.length > 1 ? `<button data-next="1" ${INC.escenario.i >= sc.versions.length - 1 ? 'disabled' : ''}>Next call → v${Math.min(sc.versions.length, INC.escenario.i + 2)}</button>` : '') +
      (INC.actual ? '<button data-clear="1">Clear</button>' : '') + (INC.escenarios.length ? '' : '<span class="hint">no scenario fits this house</span>')
  }
  if (!ov) { ban.style.display = 'none'; body.innerHTML = '<div class="hint">No incident overlay received.</div>'; return }
  ban.style.display = 'block'
  const versiones = [...INC.versions.keys()].sort((a, b) => a - b)
  const avisos = []
  for (const o of ov.occupants || []) { const L = lugar(o); if (L && L.floorOnly) avisos.push([IC.green, `${o.status === 'contradicted' ? '? ' : ''}Occupants reported on floor ${L.fi + 1} (room unknown)`]); else if (!L) avisos.push([IC.green, `Occupants reported (${nOcc(o)}), ${o.location.kind === 'floor' ? `floor ${o.location.floor_reported ?? '?'} not in the scan` : 'location unknown'}`]) }
  for (const f of ov.fire || []) { const L = lugar(f); if (L && L.floorOnly) avisos.push([IC.red, `${f.kind.toUpperCase()} reported on floor ${L.fi + 1} (room unknown)`]); else if (!L) avisos.push([IC.red, `${f.kind.toUpperCase()} reported, location unknown`]) }
  for (const c of ov.contradictions || []) avisos.push([IC.amber, `Contradiction: ${c.key} (${c.group_id}) not resolved by the dispatcher`])
  if (INC.rutas) for (const id of INC.rutas.noRoute) avisos.push(['#e5e7eb', `No route computable (${id})`])
  else if ((ov.objectives || []).length) avisos.push(['#e5e7eb', 'No route computable (no interior scan)'])
  // compacto y plegable: una linea (version y numero de elementos) que se despliega al tocar; plegado por defecto en pantallas
  // de 1180 px o menos para no tapar el 3D; el estado elegido se conserva entre versiones
  const abierto = INC.bannerAbierto ?? !document.documentElement.classList.contains('compacto')
  const nItems = (ov.changes_since_last || []).length + avisos.length
  ban.classList.toggle('plegado', !abierto)
  ban.innerHTML = `<div class="inc-fila"><button class="inc-tog" data-inctog="1" aria-expanded="${abierto}"><b>INCIDENT</b><span class="hint">v${ov.version} · ${nItems} item${nItems === 1 ? '' : 's'}</span><span class="inc-flecha" aria-hidden="true">${abierto ? '▴' : '▾'}</span></button>` +
    `<span class="inc-vers">${versiones.map((v) => `<button data-incv="${v}" class="${v === ov.version ? 'on' : ''}">v${v}</button>`).join('')}</span></div>` +
    `<div class="inc-cuerpo"><div class="hint">${esc(ov.incident_id)} · as of ${esc(String(ov.as_of).slice(11, 16))}Z</div>` +
    ((ov.changes_since_last || []).length ? `<div class="inc-cambios">${ov.changes_since_last.map((c) => `<div>● ${esc(c)}</div>`).join('')}</div>` : '') +
    avisos.map(([col, t]) => `<div class="inc-aviso" style="border-color:${col};color:${col}">${esc(t)}</div>`).join('') +
    (INC.rutas ? `<div class="hint">${INC.rutas.routes.length} route(s) · planner ${INC.rutas.ms} ms</div>` : '') +
    `<div class="inc-advisory">${AVISO}</div></div>`
  body.innerHTML = (ov.considerations || []).map((c) => `<button class="inc-card ${c.refs.length && c.refs.every((r) => INC.resaltar.has(r)) ? 'on' : ''}" data-con="${esc(c.id)}">` +
    `<div class="inc-card-t">${esc(c.text)}</div><div class="hint">${esc(c.rationale)}</div>` +
    `<div class="inc-card-m">confidence ${Math.round(c.confidence * 100)}% · evidence ${esc(c.evidence_ids.join(', '))}${c.refs.length ? ' · refs ' + esc(c.refs.join(', ')) : ''}</div></button>`).join('') || '<div class="hint">No considerations.</div>'
}

// ---------------------------------------------------------------- carga (catalogo samples/index.json + datos en vivo de la API)
function fallo(msg) {
  $('vista3d').style.opacity = 0.2; $('der').style.opacity = 0.4; $('izq').innerHTML = `<div class="nota">${esc(msg)}</div>`; $('carga').textContent = ''
  estadoEmbed(msg); avisarPadre({ type: 'error', message: msg })
}
async function cargar(id) {
  const t0 = performance.now()
  S.id = id; S.sel = null; S.hover = null; S.vb = null
  INC.index = null // mientras carga, un setIncident queda pendiente hasta tener el indice del plano nuevo
  $('carga').textContent = 'Loading…'; $('inspector').style.display = 'none'; estadoEmbed('Loading house from Matterport…')
  let doc
  try {
    if (!/^[a-z0-9-]+$/.test(id)) throw new Error('Unknown sample')
    // plano, calidad y escaleras generados en vivo desde la API de Matterport (plugin apps/web/server/house-data.mjs)
    const r = await fetch('/api/house-viewer/sample/' + encodeURIComponent(id))
    const j = await r.json().catch(() => null)
    if (!r.ok) throw new Error(j && j.error ? 'Floor plan unavailable: ' + j.error : r.status >= 500 ? 'House data server not reachable (npm run dev or npm run preview)' : 'Sample not found (HTTP ' + r.status + ')')
    doc = j
    if (doc && doc.no_scan) return sinEscaneo(id, doc)
    if (!doc || !doc.plan || !Array.isArray(doc.plan.floors) || !doc.plan.floors.length) throw new Error('The API returned no floor plan')
  } catch (e) { return fallo(e.message === 'Failed to fetch' ? 'House data server not reachable' : e.message) }
  const plan = doc.plan, t1 = performance.now()
  S.fila = doc.quality || null
  const okCasa = S.fila && S.fila.estado === 'ok'
  $('calidad').className = 'chip ' + (okCasa ? 'ok' : 'mal'); $('calidad').textContent = S.fila ? (okCasa ? 'quality: ok' : 'quality: discarded') : 'quality: unknown'
  if (!okCasa) return fallo('This sample did not pass the house quality check')
  $('vista3d').style.opacity = 1; $('der').style.opacity = 1; $('vista3d').style.visibility = ''; quitarAvisoSinEscaneo()
  S.plan = plan; S.modelId = doc.matterportModelId; S.scan = doc.scan || null
  cargarOblicuas(id)
  S.f2d = primeraPlanta(plan); S.foco = 'all' // abre con todas las plantas; el 2D muestra la planta mas baja con habitaciones
  S.ev = evacuacion(plan); const t2 = performance.now()
  ;({ niv: S.niveles, est: S.nivelEst, alt: S.alturas } = calcularNiveles(plan))
  S.tipos = new Set(plan.floors.flatMap((f) => salasDe(f).map(tipoKey)))
  if (!vistaListo) { iniciar3D(); vistaListo = true }
  panelIzq(); selectoresPlanta(); construir3D(); const t3 = performance.now()
  render2D(); tabla(); inspector(); const t4 = performance.now()
  S.ms = { fetch: Math.round(t1 - t0), evac: Math.round(t2 - t1), scene3d: Math.round(t3 - t2), plan2d_tabla: Math.round(t4 - t3), total: Math.round(t4 - t0) }
  $('carga').textContent = `Loaded in ${S.ms.total} ms`
  estadoEmbed('')
  avisoHud()
  avisarPadre({ type: 'ready', sample: id, floors: plan.floors.length, mode: EMBED ? (SPLIT ? 'split' : HOUSE ? 'house' : MODO) : null })
  prepararIncidente(plan)
  setTimeout(medirRendimiento, 2200) // los fps se cuentan cada segundo en el bucle; se miran tras 2 s
  window.__casa = { S, THREE, ms: S.ms, id, INC, setFoco, setDetalle, ponerHover, get refs() { return refs }, get P() { return P }, get escG() { return escG }, get explG() { return explG }, incSprites: () => incG.filter((o) => o.isSprite).length, // solo para las pruebas automaticas
    medir(n, fx) { const gl = renderer.getContext(), px = new Uint8Array(4), t0 = performance.now(); for (let i = 0; i < n; i++) { if (fx) composer.render(); else renderer.render(escena, camara); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px) } return (performance.now() - t0) / n } }
}

// edificio sin escaneo interior: nunca se dibuja un plano; solo banners, consideraciones e imagenes aereas
function quitarAvisoSinEscaneo() { const a = $('sin-escaneo'); if (a) a.remove() }
function sinEscaneo(id, doc) {
  S.plan = null; S.modelId = null; S.sel = null
  if (escena) limpiarIncidente3D()
  $('vista3d').style.visibility = 'hidden'; $('vista2d').innerHTML = ''
  quitarAvisoSinEscaneo()
  const a = document.createElement('div'); a.id = 'sin-escaneo'
  a.innerHTML = `<b>No interior scan on file</b><span>${esc(doc.address || id)}</span><span class="hint">Interior sections are gaps, not guesses. Only floor-level reports and considerations are shown.</span>`
  $('centro').appendChild(a)
  $('calidad').className = 'chip'; $('calidad').textContent = 'no scan'; $('carga').textContent = ''
  estadoEmbed(''); cargarOblicuas(id)
  avisarPadre({ type: 'ready', sample: id, floors: 0, mode: EMBED ? (SPLIT ? 'split' : HOUSE ? 'house' : MODO) : null, no_scan: true })
  prepararIncidente(null)
}

function medirRendimiento() {
  if (!S.plan || SOLO_2D || !S.fps || S.fps >= 30 || S.efectosReducidos) return
  S.efectosReducidos = true; S.fx = false; S.capas.points = false
  OP.wallOuterFill = Math.min(OP.wallOuterFill, 0.06); OP.wallInnerFill = 0
  panelIzq(); aplicarPlantas(); avisoHud()
}

let LISTA = [] // samples/index.json
async function inicio() {
  if (SOLO_2D) $('der').appendChild($('inspector')) // en la vista embebida 2D el inspector va sobre el plano
  if (SPLIT) montarSplit()
  if (HOUSE) montarCasa()
  eventos(); zoomPan2D()
  try {
    const r = await fetch('samples/index.json')
    if (!r.ok) throw new Error()
    LISTA = await r.json()
  } catch (e) { return fallo('Sample list could not be loaded') }
  const ids = LISTA.map((x) => x.id), pedido = Q.get('sample')
  const id = pedido || ids[0]
  $('modelo').innerHTML = LISTA.map((x) => `<option value="${esc(x.id)}" ${x.id === id ? 'selected' : ''}>${esc(x.address || x.id)}</option>`).join('')
  if (!ids.includes(id)) return fallo('Unknown sample: ' + id)
  await cargar(id)
}
inicio()
