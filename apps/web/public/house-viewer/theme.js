// Sistema de diseno compartido de casa.html (3D, plano 2D, paneles y leyendas). MONOCROMO:
// un unico tono HUE y todo lo demas se calcula con HSL cambiando solo saturacion y luminosidad.
// Unica excepcion: gris neutro para "sin ruta calculable". Cambiar HUE recolorea todo.
// Personalizable con variables CSS en :root (todas con valor por defecto): --hv-hue (tono) y --hv-<clave> para
// cualquier color de T (p. ej. --hv-bg0, --hv-outer, --hv-sel). Sin variables, el aspecto es el de siempre.

const CSSV = typeof document !== 'undefined' ? getComputedStyle(document.documentElement) : null
const cssVar = (k, def) => { const v = CSSV && CSSV.getPropertyValue('--hv-' + k).trim(); return v || def }
export let HUE = Number(cssVar('hue', '')) || 205 // azul grisaceo; aplicarTema() puede cambiarlo al tono de la app

export const hsl = (s, l, a = 1) => (a >= 1 ? `hsl(${HUE}, ${s}%, ${l}%)` : `hsla(${HUE}, ${s}%, ${l}%, ${a})`)

// Jerarquia de la planta: todo del mismo tono, solo cambian saturacion y luminosidad
const geometria = () => ({
  outer: hsl(45, 80),   // contorno exterior: brillante
  inner: hsl(24, 55),   // paredes interiores: medias
  slab: hsl(30, 60),    // losas: mismo tinte para todas, muy tenue
  door: hsl(55, 90),    // puertas: contorno fino brillante
  window: hsl(30, 64),  // ventanas: discontinua media
  stairs: hsl(35, 74),  // escaleras: rayado diagonal
  heatLo: hsl(40, 16),  // mapa de calor: cerca de la salida (oscuro)
  heatHi: hsl(70, 80),  // mapa de calor: lejos (brillante)
})

export const T = {
  // fondo y paneles
  bg0: hsl(32, 3), bg1: hsl(30, 8),
  panel: hsl(30, 5, 0.9), panelHi: hsl(30, 12), border: hsl(25, 60, 0.13), borderHi: hsl(35, 70, 0.4),
  text: hsl(22, 82), textDim: hsl(14, 54), textFaint: hsl(12, 32),
  ...geometria(),
  sel: '#ffffff',       // seleccion y ruta: blanco con resplandor
  noRoute: 'hsl(0, 0%, 42%)', // excepcion: gris neutro para "sin ruta calculable"
  accent: hsl(45, 80),  // boton activo
  // estado de un dato (mismo estilo que los chips de la app): por defecto, dentro del monocromo
  verified: hsl(45, 80), inferred: hsl(24, 55), unverified: 'hsl(0, 0%, 42%)',
  font: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
  fontUi: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
}
for (const k of Object.keys(T)) T[k] = cssVar(k, T[k])

// ---------------------------------------------------------------- tema de la app que contiene el visor (postMessage 'theme')
// Tokens aceptados (variables CSS de la app) y a que clave de T van. El tono de --color-accent pasa a ser el HUE del
// monocromo, asi que la jerarquia de la planta se conserva y solo cambia de tono.
export const TOKENS = {
  '--color-bg': ['bg0', 'bg1'], '--color-panel': ['panel'], '--color-panel-hi': ['panelHi'], '--color-line': ['border'],
  '--color-line-hi': ['borderHi'], '--color-ink': ['text'], '--color-dim': ['textDim'], '--color-faint': ['textFaint'],
  '--color-accent': ['accent'], '--color-verified': ['verified'], '--color-inferred': ['inferred'], '--color-unverified': ['unverified'],
  '--font-mono': ['font'], '--font-sans': ['fontUi'],
}
function tonoDe(c) {
  let m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c), r, g, b
  if (m) { const h = m[1].length === 3 ? m[1].replace(/./g, '$&$&') : m[1]; [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) }
  else if ((m = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i.exec(c))) [r, g, b] = m.slice(1, 4).map(Number)
  else return null
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn
  if (!d) return null
  const h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4
  return Math.round((h * 60 + 360) % 360)
}
// Aplica los tokens validos; devuelve false si no habia ninguno. Sin llamada, el visor usa su tema por defecto.
export function aplicarTema(vars) {
  if (!vars || typeof vars !== 'object') return false
  const ok = Object.entries(vars).filter(([k, v]) => TOKENS[k] && typeof v === 'string' && v.length < 300 && !/[;{}<>]/.test(v) && v.trim())
  if (!ok.length) return false
  const tono = tonoDe(String(vars['--color-accent'] || '').trim())
  if (tono != null) { HUE = tono; Object.assign(T, geometria()) }
  for (const [k, v] of ok) TOKENS[k].forEach((clave) => { T[clave] = v.trim() })
  aplicarCSS()
  document.documentElement.classList.add('tema-app')
  return true
}

export const OP = {
  wallOuterFill: 0.13, wallInnerFill: 0.05,   // planos de pared translucidos
  wallOuterLine: 0.95, wallInnerLine: 0.45,   // lineas de arriba/abajo y verticales
  slab: 0.07, slabHover: 0.22, slabSel: 0.2, slabHeat: 0.62,
  door: 1, doorArc: 0.3, window: 0.75, stairs: 0.5,
  points: 0.3,
  ghostLine: 0.11, aboveLine: 0.025,          // plantas no enfocadas: por debajo / por encima
  label: 0.7, label3d: 0.92, floorLabel: 0.55,       // label3d: rotulos 3D casi opacos para que se lean
  bbox: 0.05, baseSlab: 0.1, stairsLine: 0.9, stairsFill: 0.1, explode: 0.5, ghostTag: 0.6,
  detailEdge: 0.55, detailPoints: 0.6,        // modo Detalle
  ghost2d: 0.12, above2d: 0.03,
}

export const FX = {
  bloomStrength: 0.8, bloomRadius: 0.35, bloomThreshold: 0.86, // umbral muy alto: solo brilla el blanco (seleccion y ruta)
  fog: 0.3, pixelRatioMax: 2,
  calmPoints: 800,                       // puntos por planta en modo Calmado (solo suelo)
  detailMin: 3000, detailMax: 8000, detailPerM2: 7,
  pointSize: 0.045, transitionMs: 300,
  explodeMax: 6,                         // vista explosionada: 0 por defecto (plantas apiladas a su nivel real)
  ctxDefault: 0.4,                       // visibilidad de las plantas no enfocadas (0-1)
  slabThick: 0.15,                       // grosor de la losa bajo cada planta (m)
  riser: 0.18,                           // contrahuella de los peldanos (m)
  camPolarDeg: 40,                       // 50 grados sobre el horizonte
  glow2d: 0.05, hatch: 0.35,             // brillo SVG (m) y paso del rayado de escaleras (m)
}

// Escala del mapa de calor en el mismo tono: oscuro (cerca de la salida) -> brillante (lejos)
export function colorDistancia(t) {
  const x = Math.max(0, Math.min(1, t))
  return hsl(40 + 40 * x, 16 + 64 * x)
}
export const escalaCSS = () => `linear-gradient(90deg, ${[0, 0.25, 0.5, 0.75, 1].map(colorDistancia).join(', ')})`

export function aplicarCSS() {
  const st = document.documentElement.style
  for (const [k, v] of Object.entries(T)) st.setProperty('--' + k, v)
  st.setProperty('--escala', escalaCSS())
}
