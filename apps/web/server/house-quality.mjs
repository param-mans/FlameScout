// House quality check of a generated floor plan. Node port of the demo's tools/calidad.py (evaluar + criterion e,
// vertical connection), same thresholds, texts and numbers. The listing data (sq ft, beds) comes from the house catalog.
// Criterion e uses the same stairs module the viewer draws with (public/house-viewer/escaleras.js).
import { pyRound, pyFixed, pySum } from "./py.mjs";
import { analizar } from "../public/house-viewer/escaleras.js";
import { esEscalera } from "../public/house-viewer/evac.js";

// ---------------------------------------------------------------- THRESHOLDS AND OPTIONS (as in calidad.py)
const SALA_MIN_M2 = 2.0;              // only rooms of this area or more count (and not Void)
const UMBRAL_CONTORNO_DUDOSO = 0.15;  // a) unreliable room area / total room area: discard above this
const UMBRAL_SIN_CLASIFICAR = 0.80;   // b) share of rooms Other or untyped: discard above this
const RATIO_SUP_MIN = 0.7, RATIO_SUP_MAX = 1.3; // c) plan area / listing area: discard outside this range
const CRITERIO_DORMITORIOS = true;    // d) bedrooms criterion on/off
const TOLERANCIA_DORMITORIOS = 1;     // d) max difference between Bedroom rooms in the plan and listing beds
const DIST_VENTANA_M = 0.5;           // tag dormitorio_sin_ventana: a window this close to the outline counts
const MODO_ESTRICTO = false;          // true: houses tagged sin_escaleras are discarded too
const CRITERIO_CONEXION_VERTICAL = true; // e) 2+ floors with a pair of consecutive floors without a fitted paired staircase
// e) In the demo this criterion had a safeguard over the whole dataset (applied only if >= 400 houses stayed ok); it was
//    active there. One house at a time has no dataset, so here it always applies.
const MAX_PLANTAS = 2;                // f) always discard houses with more floors than this
const M2_A_SQFT = 10.7639;
const TIPOS_ESCALERA = ["Staircase", "Stairs"];

function distPuntoSegmento(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
  const t = L2 < 1e-12 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}
const distAContorno = (p, pol) => Math.min(...pol.map((_, i) => distPuntoSegmento(p, pol[i], pol[(i + 1) % pol.length])));

/** Metrics, criteria and tags of one house. plano = generated plan; anuncio = { sqft, beds } from the listing. */
export function evaluar(plano, anuncio = {}) {
  const pisos = plano.floors;
  const todas = pisos.flatMap((f) => f.rooms.map((r) => [f, r]));
  const salas = todas.filter(([, r]) => r.type !== "Void" && (r.areaM2 || 0) >= SALA_MIN_M2);
  const areaTotal = pySum(salas.map(([, r]) => r.areaM2));
  const areaDudosa = pySum(salas.filter(([, r]) => r.polygonQuality === "unreliable").map(([, r]) => r.areaM2));
  const pctDudosa = areaTotal ? areaDudosa / areaTotal : null;
  const sinClasificar = salas.filter(([, r]) => !r.type || r.type === "Other").length;
  const pctSin = salas.length ? sinClasificar / salas.length : null;
  const supPlano = areaTotal * M2_A_SQFT;
  const ratio = anuncio.sqft ? supPlano / anuncio.sqft : null;
  const dorm = salas.filter(([, r]) => r.type === "Bedroom");
  const escalera = todas.some(([, r]) => TIPOS_ESCALERA.includes(r.type));
  // bedrooms with a window: distance from the center of a window on its floor to the outline <= DIST_VENTANA_M
  let evaluados = 0, conVentana = 0;
  for (const [f, r] of dorm) {
    if (r.polygonQuality !== "ok" || !r.polygon || !r.polygon.length) continue; // no reliable outline: cannot measure
    evaluados++;
    if (f.openings.some((o) => o.type === "window" && distAContorno(o.center, r.polygon) <= DIST_VENTANA_M)) conVentana++;
  }
  const c = {
    contornos_dudosos: pctDudosa !== null && pctDudosa > UMBRAL_CONTORNO_DUDOSO ? 1 : 0,
    // no useful rooms means nothing to classify: unusable
    clasificacion_inutil: pctSin === null || pctSin > UMBRAL_SIN_CLASIFICAR ? 1 : 0,
    superficie_fuera_de_rango: ratio !== null && !(RATIO_SUP_MIN <= ratio && ratio <= RATIO_SUP_MAX) ? 1 : 0,
    dormitorios_incoherentes: CRITERIO_DORMITORIOS && dorm.length && anuncio.beds !== null && anuncio.beds !== undefined
      && Math.abs(dorm.length - anuncio.beds) > TOLERANCIA_DORMITORIOS ? 1 : 0,
  };
  const et = [];
  if (pisos.length >= 2 && !escalera) et.push("sin_escaleras");
  if (evaluados > conVentana) et.push("dormitorio_sin_ventana");
  if (!dorm.length) et.push("sin_dormitorios_detectados");
  c.tres_o_mas_plantas = pisos.length > MAX_PLANTAS ? 1 : 0; // f) always
  c.sin_conexion_vertical = 0;
  const motivos = [];
  if (c.contornos_dudosos) motivos.push(`Unreliable room outlines (${pyFixed(100 * pctDudosa, 0)}% of room area)`);
  if (c.clasificacion_inutil) motivos.push(`Room types unusable (${pctSin === null ? "no rooms" : `${pyFixed(100 * pctSin, 0)}%`} of rooms are Other or untyped)`);
  if (c.superficie_fuera_de_rango) motivos.push(`Plan area is ${pyFixed(ratio, 2)}x the listing area`);
  if (c.dormitorios_incoherentes) motivos.push(`Plan has ${dorm.length} bedrooms, listing says ${anuncio.beds}`);
  if (c.tres_o_mas_plantas) motivos.push("3+ floors not supported");
  if (MODO_ESTRICTO && et.includes("sin_escaleras")) motivos.push("No staircase in a multi-floor plan (strict mode)");

  // e) vertical connection, with the stairs analysis the viewer draws (same as tools/escaleras.mjs + aplicar_conexion_vertical)
  const stairs = analizar(plano);
  if (CRITERIO_CONEXION_VERTICAL && pisos.length >= 2 && !stairs.conectado.every(Boolean)) {
    const hay = pisos.some((f) => f.rooms.some(esEscalera));
    const rotos = stairs.conectado.map((ok, i) => (ok ? null : i + 1)).filter((x) => x !== null);
    c.sin_conexion_vertical = 1;
    motivos.push(`No vertical connection between floors ${rotos.map((p) => `${p}-${p + 1}`).join(", ")}`
      + (hay ? "" : " (no staircase room in the plan)") + (hay ? " (staircase not paired, or its footprint does not fit inside its room and the house)" : ""));
  }
  const descartada = Object.values(c).some(Boolean) || (MODO_ESTRICTO && et.includes("sin_escaleras"));
  return {
    quality: {
      estado: descartada ? "descartada" : "ok", ...Object.fromEntries(Object.entries(c).map(([k, v]) => [`c_${k}`, v])),
      etiquetas: et.join("|"), motivo: motivos.join("; "), plantas: pisos.length, habitaciones: salas.length,
      area_habitaciones_m2: pyRound(areaTotal, 1), area_contornos_dudosos_m2: pyRound(areaDudosa, 1),
      pct_area_dudosa: pctDudosa === null ? "" : pyRound(100 * pctDudosa, 1), pct_sin_clasificar: pctSin === null ? "" : pyRound(100 * pctSin, 1),
      superficie_plano_sqft: pyRound(supPlano), superficie_anuncio_sqft: anuncio.sqft || "", ratio_superficie: ratio === null ? "" : pyRound(ratio, 3),
      dormitorios_plano: dorm.length, dormitorios_anuncio: anuncio.beds ?? "", escalera_detectada: escalera ? 1 : 0,
      dormitorios_evaluados: evaluados, dormitorios_con_ventana: conVentana,
    },
    stairs: {
      connected: stairs.conectado,
      pairs: stairs.pares.map((E) => ({ lowerFloor: E.fi, lowerRoom: E.r.id, upperRoom: E.q.id, state: E.estado, scale: E.escala,
        direction: E.dir, directionConfidence: E.conf, reason: E.motivo })),
    },
  };
}
