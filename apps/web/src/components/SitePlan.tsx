import type { Brief, Marker, MarkerKind } from "../types/schemas";
import { markerXY } from "../mock/s2";
import { statusColorVar } from "./ui";

// SVG placeholder for the hero visual. Swap for MapLibre (map) / Matterport (3D) later; keep the
// props contract: brief in, selected key + onSelect out.

const GLYPH: Record<MarkerKind, string> = {
  stair: "S", entry: "E", exit: "X", elevator: "L", key_box: "K", roof_access: "R", fdc: "F", standpipe: "P", other: "·",
};

const PX_PER_M = 6; // placeholder scale for exposure gaps
const BLDG = { x: 300, y: 170, w: 220, h: 170 };

export interface Layers { markers: boolean; exposures: boolean; route: boolean }

export function SitePlan({
  brief, layers, selectedKey, highlightIds, onSelect,
}: {
  brief: Brief;
  layers: Layers;
  selectedKey: string | null;
  highlightIds: Set<string>;
  onSelect: (kind: "marker" | "exposure", id: string) => void;
}) {
  const east = brief.exposures.find((e) => e.side === "East") as { distance_m: number; band: string } | undefined;
  const north = brief.exposures.find((e) => e.side === "North") as { distance_m: number; band: string } | undefined;
  const footprint = `${BLDG.x},${BLDG.y} ${BLDG.x + BLDG.w},${BLDG.y} ${BLDG.x + BLDG.w},${BLDG.y + BLDG.h} ${BLDG.x + 120},${BLDG.y + BLDG.h} ${BLDG.x + 120},${BLDG.y + 130} ${BLDG.x},${BLDG.y + 130}`;

  const exposure = (side: string, d: { distance_m: number; band: string }, x: number, y: number, w: number, h: number, dim: [number, number, number, number]) => {
    const key = `exposure:${side}`;
    const on = selectedKey === key;
    return (
      <g key={key} onClick={() => onSelect("exposure", side)} className="cursor-pointer">
        <rect x={x} y={y} width={w} height={h} fill="#161c24" stroke={on ? "var(--color-accent)" : "#364353"} strokeWidth={on ? 1.5 : 1} />
        <text x={x + 8} y={y + 16} className="fill-dim" fontSize="10" fontWeight="600" letterSpacing="1">EXP {side.toUpperCase()}</text>
        <line x1={dim[0]} y1={dim[1]} x2={dim[2]} y2={dim[3]} stroke="var(--color-reported)" strokeWidth="1" />
        <text x={x + 8} y={y + 32} className="num" fill="var(--color-reported)" fontSize="11">
          {d.distance_m} m · {d.band.toUpperCase()}
        </text>
      </g>
    );
  };

  return (
    <svg viewBox="0 0 800 520" className="h-full w-full bg-bg" role="img" aria-label="Site plan placeholder">
      <defs>
        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M40 0H0V40" fill="none" stroke="#161c24" strokeWidth="1" />
        </pattern>
        <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0 0L10 5L0 10z" fill="var(--color-accent)" />
        </marker>
      </defs>
      <rect width="800" height="520" fill="url(#grid)" />

      {/* streets */}
      <rect x="0" y="392" width="800" height="56" fill="#0d1218" stroke="#222b36" />
      <text x="16" y="424" className="fill-faint" fontSize="10" letterSpacing="2">WILSHIRE BLVD</text>
      <rect x="86" y="0" width="40" height="392" fill="#0d1218" stroke="#222b36" />

      {/* subject building */}
      <polygon points={footprint} fill="#1b2430" stroke="#6f7f94" strokeWidth="1.5" />
      <text x={BLDG.x + 10} y={BLDG.y + 22} className="fill-ink" fontSize="12" fontWeight="600" letterSpacing="1">WILSHIRE TOWER</text>
      <text x={BLDG.x + 10} y={BLDG.y + 38} className="num fill-dim" fontSize="10">12 FL · OFFICE</text>

      {layers.exposures && east && exposure("East", east, BLDG.x + BLDG.w + east.distance_m * PX_PER_M, 190, 130, 130,
        [BLDG.x + BLDG.w, 255, BLDG.x + BLDG.w + east.distance_m * PX_PER_M, 255])}
      {layers.exposures && north && exposure("North", north, 320, 170 - north.distance_m * PX_PER_M - 50, 180, 50,
        [410, 170, 410, 170 - north.distance_m * PX_PER_M])}

      {layers.route && (
        <g>
          <polyline points="30,432 360,432 360,350" fill="none" stroke="var(--color-accent)" strokeWidth="1.5" strokeDasharray="6 4" markerEnd="url(#arrow)" />
          <text x="40" y="468" className="fill-accent" fontSize="10" letterSpacing="1">APPROACH (ILLUSTRATIVE)</text>
        </g>
      )}

      {layers.markers && brief.markers.map((m: Marker) => {
        const [x, y] = markerXY[m.id] ?? [BLDG.x + 20, BLDG.y + 20];
        const key = `marker:${m.id}`;
        const sel = selectedKey === key;
        const hi = highlightIds.has(m.id);
        const dashed = m.status === "unverified";
        return (
          <g key={m.id} onClick={() => onSelect("marker", m.id)} className="cursor-pointer">
            {(sel || hi) && <rect x={x - 13} y={y - 13} width="26" height="26" fill="none" stroke={sel ? "var(--color-accent)" : "var(--color-line-hi)"} strokeWidth="1.5" />}
            <rect x={x - 8} y={y - 8} width="16" height="16" fill="#0a0d11" stroke={statusColorVar(m.status)} strokeWidth="1.5" strokeDasharray={dashed ? "3 2" : undefined} />
            <text x={x} y={y + 4} textAnchor="middle" className="num fill-ink" fontSize="10" fontWeight="700">{GLYPH[m.kind]}</text>
            <text x={x + 14} y={y + 4} className="fill-dim" fontSize="10">{m.label}</text>
          </g>
        );
      })}

      {/* north arrow + scale */}
      <g transform="translate(756,40)">
        <path d="M0 -20L6 0L0 -4L-6 0z" fill="#8d97a6" />
        <text y="14" textAnchor="middle" className="fill-dim" fontSize="10">N</text>
      </g>
      <g transform="translate(600,500)">
        <line x1="0" y1="0" x2="60" y2="0" stroke="#8d97a6" />
        <text x="66" y="3" className="num fill-dim" fontSize="10">10 m (placeholder)</text>
      </g>
    </svg>
  );
}
