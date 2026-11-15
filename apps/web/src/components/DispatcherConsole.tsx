import { useState, type ReactNode } from "react";
import { factLabel } from "../labels";
import { segKey } from "../api/useLiveIncident";
import type { BuildingCandidate, CallSegment, ExtractedFact, IncidentRecord, Progress } from "../types/schemas";
import { PlanList } from "./PlanList";
import { LocationStatus, useShowConfirm } from "./ConfirmGate";
import { ExtractedChips, TranscriptComposer } from "./TranscriptComposer";
import { Btn, KV, Panel, fmt } from "./ui";

interface Props {
  segments: CallSegment[];
  incident: IncidentRecord;
  candidates: BuildingCandidate[];
  confirmedId: string | null;
  onConfirm: (id: string) => void;
  /** Collapsible demo scaffolding (sim data, sample house), rendered above the console. */
  demo?: ReactNode;
  /** Live mode: dispatcher actions and orchestrator state. Absent in mock mode. */
  onResolve?: (key: string, value: unknown) => void;
  questions?: string[];                       // copilot suggestions; default: incident.open_questions
  progress?: Progress | null;
  busy?: string | null;
  held?: { version: number; needsReview: string[] } | null;
  onApproveHeld?: () => void;
  /** Live mode: type a transcript line, and see what the extractor understood from each caller line. */
  onSend?: (text: string, speaker: CallSegment["speaker"], callId: string) => void;
  extracted?: Record<string, ExtractedFact[]>;
}

export function DispatcherConsole({
  segments, incident, candidates, confirmedId, onConfirm, demo, onResolve, questions, progress, busy, held, onApproveHeld,
  onSend, extracted,
}: Props) {
  // Dispatcher's choice per contradicted key, shown at once; the API confirms it with incident.updated.
  const [resolved, setResolved] = useState<Record<string, unknown>>({});
  const isResolved = (key: string) => key in resolved;
  const showConfirm = useShowConfirm(incident, candidates, confirmedId);   // hidden after an automatic confirmation

  // Text one step larger than the shared components' defaults (Panel, KV, Btn, StatusTag, PlanList are also used by the
  // MDT, which keeps its sizes): every size class below this root moves up (labels 15 px, body 16-18 px).
  return (
    // Below lg (phones, iPad portrait): one scrolling column. lg+: three columns that scroll on their own.
    <div className={String.raw`flex h-full flex-col overflow-y-auto lg:overflow-hidden text-[18px] [&_.label]:!text-[15px] [&_.text-\[10px\]]:!text-[15px] [&_.text-\[11px\]]:!text-[16px] [&_.text-\[12px\]]:!text-[17px] [&_.text-\[13px\]]:!text-[18px] [&_.text-\[14px\]]:!text-[18px] [&_.text-\[15px\]]:!text-[20px] [&_.text-\[16px\]]:!text-[20px]`}>
      {demo}
      {held && (
        <div className="num flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-reported/50 bg-reported/10 px-3 py-1.5 text-[12px] text-reported">
          <span className="font-semibold">■ BRIEF V{held.version} HELD FOR REVIEW</span>
          <span className="text-dim">{held.needsReview.length ? `Low confidence: ${held.needsReview.join(", ")}` : "A consideration is below 0.5 confidence."}</span>
          {onApproveHeld && <span className="ml-auto"><Btn onClick={onApproveHeld}>Publish as is</Btn></span>}
        </div>
      )}
      <div className="grid flex-none grid-cols-[minmax(0,1fr)] gap-px bg-line lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_300px_400px]">
        <div className="flex min-h-[50vh] flex-col gap-px bg-line lg:min-h-0">
          <Panel title="Live calls" right={`${incident.call_ids.length} calls`} className="flex-1">
            {segments.length === 0 && <div className="p-3 text-dim">{onSend ? "Listening… run a call, or type a line below." : "Listening…"}</div>}
            {segments.map((s, i) => {
              const ex = extracted?.[segKey(s)];
              return (
                <div key={i} className="grid grid-cols-[92px_1fr] gap-2 border-b border-line px-2.5 py-1.5">
                  <span className="num text-[11px] text-dim">
                    {s.call_id.toUpperCase()} {s.speaker === "caller" ? "CALLER" : "CALL-TAKER"}
                    <br />
                    <span className="text-faint">{s.t_start.toFixed(1)}s</span>
                  </span>
                  <span>
                    <span className={s.speaker === "caller" ? "text-ink" : "text-dim"}>{s.text}</span>
                    {s.speaker === "caller" && ex && <ExtractedChips facts={ex} />}
                  </span>
                </div>
              );
            })}
          </Panel>
          {onSend && <TranscriptComposer callIds={incident.call_ids} onSend={onSend} />}
        </div>

        <Panel title="Incident record" right={`${Object.keys(incident.facts).length} facts`} className="order-3 lg:order-none">
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
                    <Btn onClick={() => { setResolved((r) => ({ ...r, [c.key]: v.value })); onResolve?.(c.key, v.value); }}>Use</Btn>
                  </div>
                ))}
              </div>
            ),
          )}
          {Object.values(incident.facts).map((f) => (
            <KV key={f.key} k={factLabel(f.key)} v={fmt(f.value)} status={f.status} confidence={f.confidence} confidenceOnHover />
          ))}
        </Panel>

        <div className="order-2 grid min-h-0 grid-rows-[auto_1fr] gap-px bg-line lg:order-none">
          {!showConfirm ? <LocationStatus incident={incident} candidates={candidates} confirmedId={confirmedId} progress={progress} busy={busy} /> : <Panel title="Confirm location" right={busy ?? (confirmedId ? "confirmed" : "awaiting")} className="shadow-[inset_0_2px_0_var(--color-accent)]">
            {candidates.length === 0 && <div className="p-3 text-dim">Awaiting address…</div>}
            {candidates.map((c) => {
              const on = confirmedId === c.building_id;
              return (
                <div key={c.building_id} className={`border-b border-line px-3 py-2.5 ${on ? "bg-accent/10" : ""}`}>
                  <div className="flex items-baseline justify-between">
                    <span className="text-[15px] font-semibold">{c.name ?? c.address}</span>
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
            <PlanList progress={progress ?? null} />
          </Panel>}
          <Panel title="Copilot" right="suggested questions">
            {(questions ?? incident.open_questions).map((q) => (
              <div key={q} className="border-b border-line px-2.5 py-1.5">{q}</div>
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}
