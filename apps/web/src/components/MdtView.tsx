import { useMemo, useState } from "react";
import type { Brief, CallSegment, Contradiction, Fact } from "../types/schemas";
import { Inspector, type Selection } from "./Inspector";
import { SitePlan, type Layers } from "./SitePlan";
import { Timeline } from "./Timeline";
import { Btn, KV, Panel, StatusTag, fmt } from "./ui";

const LABELS: Record<string, string> = {
  "building.use": "Use",
  "building.stories": "Stories",
  "building.year_built": "Year built",
  "building.area_sqft": "Area sq ft",
  "building.height_est": "Height",
  "incident.floor": "Rpt. floor",
};

type Exposure = { side: string; distance_m: number; band: string };

function TopBar({ brief }: { brief: Brief }) {
  const { scan_age_days, costar_as_of } = brief.freshness;
  const scan = scan_age_days == null ? "NONE" : `${Math.round(scan_age_days / 30)} MO`;
  const cell = "border-l border-line px-3 leading-8";
  return (
    <header className="flex h-8 items-center border-b border-line bg-panel">
      <div className="px-3 text-[13px] font-semibold">WILSHIRE TOWER</div>
      <div className="num text-[12px] text-dim">1200 Wilshire Blvd, Los Angeles</div>
      <div className={`${cell} num text-[11px] font-semibold ${brief.location_confirmed ? "text-verified" : "text-reported"}`}>
        {brief.location_confirmed ? "● LOCATION CONFIRMED" : "■ AWAITING CONFIRMATION"}
      </div>
      <div className={`${cell} label`}>Structure fire · smoke reported</div>
      <div className="ml-auto flex">
        <div className={`${cell} num text-[11px] text-dim`}>SCAN {scan}</div>
        <div className={`${cell} num text-[11px] text-dim`}>COSTAR {fmt(costar_as_of).toUpperCase()}</div>
        <div className={`${cell} num text-[11px] font-semibold ${brief.changes_since_last.length ? "text-accent" : "text-dim"}`}>
          V{brief.version}
          {brief.changes_since_last.length > 0 && " · CHG"}
        </div>
      </div>
    </header>
  );
}

function Readout({ label, value, sub, tone = "text-ink", onClick }: { label: string; value: string; sub?: React.ReactNode; tone?: string; onClick?: () => void }) {
  return (
    <button onClick={onClick} className="bg-panel px-3 py-2 text-left hover:bg-panel-hi">
      <div className="label">{label}</div>
      <div className={`num text-[28px] font-semibold leading-tight ${tone}`}>{value}</div>
      <div className="h-4 text-[11px] text-dim">{sub}</div>
    </button>
  );
}

export function MdtView({ brief, segments, contradictions }: { brief: Brief; segments: CallSegment[]; contradictions: Contradiction[] }) {
  const [sel, setSel] = useState<Selection | null>(null);
  const [tab, setTab] = useState<"map" | "floor" | "scan3d">("map");
  const [layers, setLayers] = useState<Layers>({ markers: true, exposures: true, route: true });

  const selectFact = (f: Fact, kind: string, label: string) =>
    setSel({ key: `fact:${f.key}`, title: `${label}: ${fmt(f.value)}`, kind, status: f.status, confidence: f.confidence, evidence_ids: f.evidence_ids,
      fields: [["Key", f.key], ["As of", f.as_of ?? "n/a"]] });

  const exposures = brief.exposures as Exposure[];
  const stairs = brief.markers.filter((m) => m.kind === "stair").length; // computed in code, not by an LLM
  const noScan = brief.markers.length === 0;
  const closest = exposures.length ? exposures.reduce((a, b) => (a.distance_m <= b.distance_m ? a : b)) : null;
  const floor = brief.size_up["incident.floor"];

  // Markers that share evidence with the current selection light up on the map.
  const highlightIds = useMemo(() => {
    const ev = new Set(sel?.evidence_ids ?? []);
    return new Set(brief.markers.filter((m) => m.evidence_ids.some((e) => ev.has(e))).map((m) => m.id));
  }, [sel, brief.markers]);

  const onMapSelect = (kind: "marker" | "exposure", id: string) => {
    if (kind === "marker") {
      const m = brief.markers.find((x) => x.id === id)!;
      setSel({ key: `marker:${m.id}`, title: m.label, kind: "Marker", status: m.status, confidence: m.confidence, evidence_ids: m.evidence_ids,
        fields: [["Type", m.kind.replace("_", " ")], ["Floor", m.floor == null ? "—" : String(m.floor)]] });
    } else {
      const e = exposures.find((x) => x.side === id)!;
      setSel({ key: `exposure:${id}`, title: `Exposure ${e.side}: ${e.distance_m} m`, kind: "Exposure", status: "inferred", evidence_ids: ["e_rule"],
        fields: [["Band", e.band], ["Distance", `${e.distance_m} m`]], note: "Distance and band are computed by the rules engine (R-03), not estimated by a model." });
    }
  };

  const toggle = (k: keyof Layers) => setLayers((l) => ({ ...l, [k]: !l[k] }));

  return (
    <div className="flex h-full flex-col">
      <TopBar brief={brief} />
      {brief.changes_since_last.length > 0 && (
        <div className="num flex gap-6 border-b border-accent/40 bg-accent/10 px-3 py-1 text-[12px] text-accent">
          <span className="font-semibold">CHANGED SINCE V{brief.version - 1}</span>
          {brief.changes_since_last.map((c) => <span key={c}>+ {c}</span>)}
        </div>
      )}

      {/* Glanceable readouts: the 5-second view. */}
      <div className="grid grid-cols-4 gap-px border-b border-line bg-line">
        <Readout label="Reported floor" value={fmt(floor?.value)} tone={floor?.status === "contradicted" ? "text-contradicted" : "text-ink"}
          sub={floor && <StatusTag status={floor.status} confidence={floor.confidence} />} onClick={() => floor && selectFact(floor, "Fact", "Reported floor")} />
        <Readout label="Stairwells identified" value={noScan ? "NO SCAN" : String(stairs)} tone={noScan ? "text-unverified" : "text-ink"}
          sub={noScan ? "interior not on file" : stairs < 2 ? "verify on arrival" : "from scan"} />
        <Readout label="Hazard flags" value={String(brief.hazards.length)} sub="from tenants (inferred)" />
        <Readout label="Closest exposure" value={closest ? `${closest.distance_m} m` : "—"} tone={closest?.band === "close" ? "text-reported" : "text-ink"}
          sub={closest && `${closest.side} · ${closest.band}`} />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[304px_1fr_340px] gap-px bg-line">
        <Panel title="Size-up" right={`${Object.keys(brief.size_up).length} facts`}>
          {Object.entries(brief.size_up).map(([k, f]) => (
            <KV key={k} k={LABELS[k] ?? k} v={fmt(f.value)} status={f.status} confidence={f.confidence}
              selected={sel?.key === `fact:${f.key}`} onClick={() => selectFact(f, "Fact", LABELS[k] ?? k)} />
          ))}
          <div className="label border-b border-line bg-bg px-2.5 py-1">Hazards</div>
          {brief.hazards.length === 0 && <div className="px-2.5 py-2 text-dim">None identified</div>}
          {brief.hazards.map((h) => (
            <KV key={h.key} k="Hazard" v={fmt(h.value)} status={h.status} confidence={h.confidence}
              selected={sel?.key === `fact:${h.key}`} onClick={() => selectFact(h, "Hazard", "Hazard")} />
          ))}
        </Panel>

        {/* Hero: map / floor / 3D. SVG placeholder for now. */}
        <section className="relative flex min-h-0 flex-col bg-bg">
          <div className="flex h-7 shrink-0 items-center justify-between border-b border-line bg-panel px-2.5">
            <div className="flex gap-1">
              <Btn active={tab === "map"} onClick={() => setTab("map")}>Map</Btn>
              <Btn active={tab === "floor"} onClick={() => setTab("floor")}>Floor 2D</Btn>
              <Btn active={tab === "scan3d"} onClick={() => setTab("scan3d")}>3D scan</Btn>
            </div>
            {tab === "map" && (
              <div className="flex gap-1">
                <Btn active={layers.markers} onClick={() => toggle("markers")}>Markers</Btn>
                <Btn active={layers.exposures} onClick={() => toggle("exposures")}>Exposures</Btn>
                <Btn active={layers.route} onClick={() => toggle("route")}>Route</Btn>
              </div>
            )}
          </div>
          <div className="relative min-h-0 flex-1">
            {tab === "map" ? (
              <SitePlan brief={brief} layers={layers} selectedKey={sel?.key ?? null} highlightIds={highlightIds} onSelect={onMapSelect} />
            ) : (
              <div className="grid h-full place-items-center text-center">
                {/* TODO: Matterport embed / normalized 2D plan with markers (PRD section 23) */}
                <div>
                  <div className={`num text-[15px] ${noScan ? "text-unverified" : "text-dim"}`}>
                    {noScan ? "○ NO INTERIOR SCAN ON FILE" : tab === "floor" ? "FLOOR 2D" : "3D SCAN"}
                  </div>
                  <div className="mt-1 text-[12px] text-faint">{noScan ? "Interior sections are gaps, not guesses." : "Placeholder. Viewer not wired yet."}</div>
                </div>
              </div>
            )}
            {noScan && tab === "map" && (
              <div className="num absolute left-3 top-3 border border-unverified/60 bg-bg/90 px-2 py-1 text-[11px] text-unverified">○ NO INTERIOR SCAN ON FILE</div>
            )}
          </div>
        </section>

        <div className="grid min-h-0 grid-rows-[1fr_1fr] gap-px bg-line">
          <Panel title="Considerations" right={`${brief.considerations.length}/5 · advisory`}>
            {brief.considerations.length === 0 && <div className="p-3 text-dim">No considerations. Facts only.</div>}
            {brief.considerations.map((c) => (
              <button
                key={c.id}
                onClick={() => setSel({ key: `cons:${c.id}`, title: c.title, kind: "Consideration", confidence: c.confidence, evidence_ids: c.evidence_ids,
                  fields: [["Rank", String(c.rank)], ["Category", c.category.replace("_", " ")]], note: c.rationale })}
                className={`grid w-full grid-cols-[20px_1fr_auto] gap-2 border-b border-line px-2.5 py-2 text-left hover:bg-panel-hi ${
                  sel?.key === `cons:${c.id}` ? "bg-panel-hi shadow-[inset_2px_0_0_var(--color-accent)]" : ""
                }`}
              >
                <span className="num text-dim">{c.rank}</span>
                <span>
                  <span className="label block">{c.category.replace("_", " ")}</span>
                  <span className="text-[13px] leading-snug">{c.title}</span>
                </span>
                <span className="num text-[11px] text-dim">{Math.round(c.confidence * 100)}</span>
              </button>
            ))}
          </Panel>
          <Inspector sel={sel} pool={brief.evidence} />
        </div>
      </div>

      <Timeline segments={segments} contradictions={contradictions} currentVersion={brief.version} />
      <footer className="flex h-7 items-center gap-3 border-t border-line bg-panel px-3 text-[12px]">
        <span className="label !text-reported">Unknown</span>
        <span className="num">{brief.gaps.length ? brief.gaps.join(" · ") : "none"}</span>
        <span className="ml-auto text-dim">{brief.disclaimer}</span>
      </footer>
    </div>
  );
}
