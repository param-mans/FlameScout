// Sistema de diseno compartido de casa.html (3D, plano 2D, paneles y leyendas). MONOCROMO:
// un unico tono HUE y todo lo demas se calcula con HSL cambiando solo saturacion y luminosidad.
// Unica excepcion: gris neutro para "sin ruta calculable". Cambiar HUE recolorea todo.
// Personalizable con variables CSS en :root (todas con valor por defecto): --hv-hue (tono) y --hv-<clave> para
// cualquier color de T (p. ej. --hv-bg0, --hv-outer, --hv-sel). Sin variables, el aspecto es el de siempre.

const CSSV = typeof document !== 'undefined' ? getComputedStyle(document.documentElement) : null
const cssVar = (k, def) => { const v = CSSV && CSSV.getPropertyValue('--hv-' + k).trim(); return v || def }
export const HUE = Number(cssVar('hue', '')) || 205 // azul grisaceo

export const hsl = (s, l, a = 1) => (a >= 1 ? `hsl(${HUE}, ${s}%, ${l}%)` : `hsla(${HUE}, ${s}%, ${l}%, ${a})`)

export const T = {
  // fondo y paneles
  bg0: hsl(32, 3), bg1: hsl(30, 8),
  panel: hsl(30, 5, 0.9), border: hsl(25, 60, 0.13), borderHi: hsl(35, 70, 0.4),
  text: hsl(22, 82), textDim: hsl(14, 54), textFaint: hsl(12, 32),
  // jerarquia de la planta
  outer: hsl(45, 80),   // contorno exterior: brillante
  inner: hsl(24, 55),   // paredes interiores: medias
  slab: hsl(30, 60),    // losas: mismo tinte para todas, muy tenue
  door: hsl(55, 90),    // puertas: contorno fino brillante
  window: hsl(30, 64),  // ventanas: discontinua media
  stairs: hsl(35, 74),  // escaleras: rayado diagonal
  sel: '#ffffff',       // seleccion y ruta: blanco con resplandor
  heatLo: hsl(40, 16),  // mapa de calor: cerca de la salida (oscuro)
  heatHi: hsl(70, 80),  // mapa de calor: lejos (brillante)
  noRoute: 'hsl(0, 0%, 42%)', // excepcion: gris neutro para "sin ruta calculable"
  font: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
}
for (const k of Object.keys(T)) T[k] = cssVar(k, T[k])

export const OP = {
  wallOuterFill: 0.13, wallInnerFill: 0.05,   // planos de pared translucidos
  wallOuterLine: 0.95, wallInnerLine: 0.45,   // lineas de arriba/abajo y verticales
  slab: 0.07, slabHover: 0.22, slabSel: 0.2, slabHeat: 0.62,
  door: 1, doorArc: 0.3, window: 0.75, stairs: 0.5,
  points: 0.3,
  ghostLine: 0.11, aboveLine: 0.025,          // plantas no enfocadas: por debajo / por encima
  label: 0.7, floorLabel: 0.55,
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
export const ESCALA_CSS = `linear-gradient(90deg, ${[0, 0.25, 0.5, 0.75, 1].map(colorDistancia).join(', ')})`

export function aplicarCSS() {
  const st = document.documentElement.style
  for (const [k, v] of Object.entries(T)) st.setProperty('--' + k, v)
  st.setProperty('--escala', ESCALA_CSS)
}
