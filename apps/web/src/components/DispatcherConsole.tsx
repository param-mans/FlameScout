import type { BuildingCandidate, CallSegment, IncidentRecord } from "../types/schemas";
import { Btn, KV, Panel, fmt } from "./ui";

interface Props {
  segments: CallSegment[];
  incident: IncidentRecord;
  candidates: BuildingCandidate[];
  confirmedId: string | null;
  onConfirm: (id: string) => void;
}

export function DispatcherConsole({ segments, incident, candidates, confirmedId, onConfirm }: Props) {
  const open = incident.contradictions.filter((c) => !c.resolved_by_dispatcher);
  return (
    <div className="grid h-full grid-cols-[1fr_1fr_340px] gap-px bg-line">
      <Panel title="Live calls" right={`${incident.call_ids.length} calls`}>
        {segments.length === 0 && <div className="p-3 text-dim">Listening…</div>}
        {segments.map((s, i) => (
          <div key={i} className="grid grid-cols-[92px_1fr] gap-2 border-b border-line px-2.5 py-1.5">
            <span className="num text-[11px] text-dim">
              {s.call_id.toUpperCase()} {s.speaker === "caller" ? "CALLER" : "TAKER"}
              <br />
              <span className="text-faint">{s.t_start.toFixed(1)}s</span>
            </span>
            <span className={s.speaker === "caller" ? "text-ink" : "text-dim"}>{s.text}</span>
          </div>
        ))}
      </Panel>

      <Panel title="Incident record" right={`${Object.keys(incident.facts).length} facts`}>
        {open.map((c) => (
          <div key={c.key} className="border-b border-contradicted/50 bg-contradicted/10 px-2.5 py-2">
            <div className="num text-[12px] font-semibold text-contradicted">▲ CONTRADICTION · {c.key}</div>
            {c.values.map((v) => (
              <div key={v.call_id} className="mt-1 grid grid-cols-[28px_40px_1fr] gap-2 text-[12px]">
                <span className="num text-dim">{v.call_id.toUpperCase()}</span>
                <span className="num">{fmt(v.value)}</span>
                <span className="italic text-dim">“{v.span.text}”</span>
              </div>
            ))}
            {/* TODO: dispatcher resolve action */}
          </div>
        ))}
        {Object.values(incident.facts).map((f) => (
          <KV key={f.key} k={f.key} v={fmt(f.value)} status={f.status} confidence={f.confidence} />
        ))}
      </Panel>

      <div className="grid min-h-0 grid-rows-[auto_1fr] gap-px bg-line">
        <Panel title="Location" right={confirmedId ? "confirmed" : "awaiting"}>
          {candidates.length === 0 && <div className="p-3 text-dim">Awaiting address…</div>}
          {candidates.map((c) => (
            <div key={c.building_id} className="border-b border-line px-2.5 py-2">
              <div className="flex items-baseline justify-between">
                <span className="font-semibold">{c.name}</span>
                <span className="num text-[11px] text-dim">{Math.round(c.score * 100)}% match</span>
              </div>
              <div className="num mb-1.5 text-[12px] text-dim">{c.address}</div>
              <Btn active={confirmedId === c.building_id} onClick={() => onConfirm(c.building_id)}>
                {confirmedId === c.building_id ? "● Confirmed" : "Confirm location"}
              </Btn>
            </div>
          ))}
          {!confirmedId && candidates.length > 0 && <div className="px-2.5 py-2 text-[12px] text-faint">No brief is produced until the dispatcher confirms.</div>}
        </Panel>
        <Panel title="Copilot" right="suggested questions">
          {incident.open_questions.map((q) => (
            <div key={q} className="border-b border-line px-2.5 py-1.5">{q}</div>
          ))}
        </Panel>
      </div>
    </div>
  );
}
