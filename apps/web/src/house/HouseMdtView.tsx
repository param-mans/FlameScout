// MDT view for a house: everything on screen comes from the listing, the floor plan and the incident overlay (houseBrief).
// Three zones at most: size-up and hazards | floor plan + 3D | considerations. Touch first (44 px targets, 12 px text).
import { useState } from "react";
import { BuildingViewer } from "../components/viewer/BuildingViewer";
import { StatusTag } from "../components/ui";
import { houseBrief, NA, type Row } from "./houseBrief";
import { useHouseData } from "./useHouseData";
import "./house.css";

function Card({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="hv-card">
      <div className="hv-label">{label}</div>
      <div className={`hv-card-value ${tone ?? ""}`}>{value}</div>
      {sub && <div className="hv-card-sub">{sub}</div>}
    </div>
  );
}
function Rows({ rows }: { rows: Row[] }) {
  return (
    <>
      {rows.map((r) => (
        <div key={r.label} className="hv-row">
          <div className="hv-row-k">{r.label}</div>
          <div className="hv-row-v">
            <span className={r.value.startsWith(NA) || r.value.startsWith("not mentioned") ? "text-dim" : ""}>{r.value}</span>
            {r.status && <span className="ml-2"><StatusTag status={r.status} /></span>}
            {r.detail && <div className="hv-row-d">{r.detail}</div>}
          </div>
        </div>
      ))}
    </>
  );
}

export function HouseMdtView({ sampleId, sampleError }: { sampleId: string; sampleError?: string | null }) {
  const { sample, doc, error, incident, onPlanIndex, onIncidentState } = useHouseData(sampleId);
  const [highlight, setHighlight] = useState<string[]>([]);
  const [picked, setPicked] = useState<string | null>(null);
  if (!sample) return <div className="hv-empty">{error ?? sampleError ?? "Loading house…"}</div>;
  const { extras: x } = houseBrief(sample, doc, incident.overlay);
  const noScan = !!(doc?.no_scan || sample.no_scan || !sample.matterportModelId);

  return (
    <div className="hv-root">
      <header className="hv-header">
        <div className="hv-title"><b>{x.header.type}</b> · {x.header.address}</div>
        {x.header.incident && <div className="hv-chip hv-chip-accent">{x.header.incident}</div>}
        <div className="hv-chip">{noScan ? "No interior scan on file" : x.scan.label}</div>
      </header>

      <div className="hv-cards">
        <Card label="Incident" value={x.cards.incident} tone={incident.overlay ? "text-reported" : "text-dim"}
          sub={incident.overlay ? `${incident.routes} planner route(s)${incident.noRoute.length ? ` · no route for ${incident.noRoute.join(", ")}` : ""}` : "Waiting for caller reports"} />
        <Card label="Sleeping areas" value={x.cards.sleeping} sub="Bedroom rooms in the scan (types detected by AI)" />
        <Card label="Exits & stairs" value={x.cards.exits} sub="Ground-floor exterior doors · stairs from the plan" />
        <Card label="Scan" value={x.cards.scan} tone={x.cards.scanPartial ? "text-reported" : ""} sub={x.cards.scanPartial ? "partial: the plan covers less than 70 % of the listing area" : "date = model creation in Matterport"} />
      </div>

      <div className="hv-main">
        <aside className="hv-side">
          <div className="hv-section">Size-up</div>
          <Rows rows={x.sizeUp} />
          <div className="hv-section">Hazards (from listing text)</div>
          <Rows rows={x.hazards} />
        </aside>
        <section className="hv-center">
          <BuildingViewer layout="split" sampleId={sampleId} sampleError={sampleError} host
            onIncidentState={onIncidentState} onPlanIndex={onPlanIndex} highlightRefs={highlight} />
        </section>
        <aside className="hv-side">
          <div className="hv-section">Considerations <span className="hv-hint">advisory</span></div>
          {x.considerations.length === 0 && <div className="hv-pad text-dim">No considerations yet. They appear when calls are processed.</div>}
          {x.considerations.map((c) => (
            <button key={c.id} className={`hv-cons ${picked === c.id ? "on" : ""}`}
              onClick={() => { const on = picked === c.id; setPicked(on ? null : c.id); setHighlight(on ? [] : c.refs); }}>
              <div className="hv-cons-t">{c.text}</div>
              <div className="hv-cons-r">{c.rationale}</div>
              <div className="hv-cons-m">
                {c.level && <span className={`hv-level hv-level-${c.level.toLowerCase()}`}>{c.level}</span>}
                <span>{c.evidence.length ? `evidence: ${c.evidence.join(", ")}` : "no evidence"}</span>
              </div>
            </button>
          ))}
        </aside>
      </div>

      <footer className="hv-footer">
        <span className="hv-label text-reported">Unknown</span>
        <span>{x.unknowns.join(" · ")}</span>
        <span className="ml-auto text-dim">Advisory only. Verify on arrival.</span>
      </footer>
    </div>
  );
}
