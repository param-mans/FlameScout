import { useState, type ReactNode } from "react";
import { factLabel } from "../labels";
import type { BuildingCandidate, CallSegment, IncidentRecord } from "../types/schemas";
import { Btn, KV, Panel, fmt } from "./ui";

interface Props {
  segments: CallSegment[];
  incident: IncidentRecord;
  candidates: BuildingCandidate[];
  confirmedId: string | null;
  onConfirm: (id: string) => void;
  /** Collapsible demo scaffolding (sim data, sample house), rendered above the console. */
  demo?: ReactNode;
}

export function DispatcherConsole({ segments, incident, candidates, confirmedId, onConfirm, demo }: Props) {
  // Dispatcher's choice per contradicted key. Local until the UI talks to the API.
  // TODO: POST /api/incidents/{id}/contradictions/resolve {key, value}
  const [resolved, setResolved] = useState<Record<string, unknown>>({});
  const isResolved = (key: string) => key in resolved;

  return (
    <div className="flex h-full flex-col">
      {demo}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_300px_400px] gap-px bg-line">
        <Panel title="Live calls" right={`${incident.call_ids.length} calls`}>
          {segments.length === 0 && <div className="p-3 text-dim">Listening…</div>}
          {segments.map((s, i) => (
            <div key={i} className="grid grid-cols-[92px_1fr] gap-2 border-b border-line px-2.5 py-1.5">
              <span className="num text-[11px] text-dim">
                {s.call_id.toUpperCase()} {s.speaker === "caller" ? "CALLER" : "CALL-TAKER"}
                <br />
                <span className="text-faint">{s.t_start.toFixed(1)}s</span>
              </span>
              <span className={s.speaker === "caller" ? "text-ink" : "text-dim"}>{s.text}</span>
            </div>
          ))}
        </Panel>

        <Panel title="Incident record" right={`${Object.keys(incident.facts).length} facts`}>
          {incident.contradictions.filter((c) => !c.resolved_by_dispatcher).map((c) =>
            isResolved(c.key) ? (
              <div key={c.key} className="num border-b border-line px-2.5 py-1.5 text-[12px] text-dim">
                ✓ {factLabel(c.key)}: <span className="text-ink">{fmt(resolved[c.key])}</span> (dispatcher)
              </div>
            ) : (
              <div key={c.key} className="border-b border-contradicted/50 bg-contradicted/10 px-2.5 py-2">
                <div className="num text-[12px] font-semibold text-contradicted">▲ CONTRADICTION · {factLabel(c.key)}</div>
                {c.values.map((v) => (
                  <div key={v.call_id} className="mt-1.5 grid grid-cols-[28px_1fr_auto] items-start gap-2 text-[12px]">
                    <span className="num text-dim">{v.call_id.toUpperCase()}</span>
                    <span>
                      <span className="num">{fmt(v.value)}</span>
                      <span className="block italic text-dim">“{v.span.text}”</span>
                    </span>
                    <Btn onClick={() => setResolved((r) => ({ ...r, [c.key]: v.value }))}>Use</Btn>
                  </div>
                ))}
              </div>
            ),
          )}
          {Object.values(incident.facts).map((f) => (
            <KV key={f.key} k={factLabel(f.key)} v={fmt(f.value)} status={f.status} confidence={f.confidence} confidenceOnHover />
          ))}
        </Panel>

        <div className="grid min-h-0 grid-rows-[auto_1fr] gap-px bg-line">
          <Panel title="Confirm location" right={confirmedId ? "confirmed" : "awaiting"} className="shadow-[inset_0_2px_0_var(--color-accent)]">
            {candidates.length === 0 && <div className="p-3 text-dim">Awaiting address…</div>}
            {candidates.map((c) => {
              const on = confirmedId === c.building_id;
              return (
                <div key={c.building_id} className={`border-b border-line px-3 py-2.5 ${on ? "bg-accent/10" : ""}`}>
                  <div className="flex items-baseline justify-between">
                    <span className="text-[15px] font-semibold">{c.name}</span>
                    <span className="num text-[12px] text-dim">{Math.round(c.score * 100)}% match</span>
                  </div>
                  <div className="num mb-2 text-[12px] text-dim">{c.address}</div>
                  <button
                    onClick={() => onConfirm(c.building_id)}
                    className={`w-full border py-2 text-[13px] font-semibold uppercase tracking-wider ${
                      on ? "border-accent bg-accent/20 text-accent" : "border-accent/70 text-ink hover:bg-accent/15"
                    }`}
                  >
                    {on ? "● Confirmed" : "Confirm location"}
                  </button>
                </div>
              );
            })}
            {!confirmedId && candidates.length > 0 && <div className="px-3 py-2 text-[12px] text-faint">No brief is produced until the dispatcher confirms.</div>}
          </Panel>
          <Panel title="Copilot" right="suggested questions">
            {incident.open_questions.map((q) => (
              <div key={q} className="border-b border-line px-2.5 py-1.5">{q}</div>
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}
