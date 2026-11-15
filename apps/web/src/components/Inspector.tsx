import { useState } from "react";
import type { Evidence, FactStatus } from "../types/schemas";
import { Btn, Panel, StatusTag, fmt } from "./ui";

/** What is currently selected anywhere in the MDT. Drives the docked inspector and map highlight. */
export interface Selection {
  key: string; // unique, e.g. "fact:building.use", "marker:m1", "cons:c1"
  title: string;
  kind: string; // "Fact" | "Marker" | "Consideration" | "Hazard" | "Exposure"
  status?: FactStatus;
  confidence?: number;
  evidence_ids: string[];
  fields?: [string, string][];
  note?: string;
}

function EvidenceItem({ e }: { e: Evidence }) {
  const loc = e.locator as { text?: string } | null | undefined;
  return (
    <li className="border-b border-line px-2.5 py-2">
      <div className="flex items-baseline justify-between">
        <span className="num text-[12px] text-ink">{e.kind.replace("_", " ").toUpperCase()}</span>
        <span className="label">{e.source}</span>
      </div>
      <div className="num mt-0.5 text-[11px] text-dim">
        {e.id} · captured {e.captured_at ? e.captured_at.slice(0, 10) : "n/a"}
      </div>
      {loc?.text && <blockquote className="mt-1.5 border-l-2 border-line-hi pl-2 text-[12px] italic text-dim">“{loc.text}”</blockquote>}
      {/* Vexcel view the vision model read (served by the house-data server for the hero buildings) */}
      {e.kind === "aerial_crop" && e.uri?.startsWith("/api/") && (
        <figure className="mt-1.5">
          <img src={e.uri} alt={`Vexcel aerial, ${e.id}`} loading="lazy" className="w-full border border-line" />
          <figcaption className="num mt-0.5 text-[10px] text-faint">© Vexcel Imaging</figcaption>
        </figure>
      )}
      {e.locator && !loc?.text && <pre className="num mt-1 whitespace-pre-wrap text-[11px] text-faint">{JSON.stringify(e.locator)}</pre>}
    </li>
  );
}

export type Verdict = "confirmed" | "wrong" | "outdated";

/** Crew feedback only exists for facts, hazards and markers; the backend keys them by fact key or marker id. */
export const feedbackItemId = (sel: Selection): string | null =>
  sel.key.startsWith("fact:") ? sel.key.slice(5) : sel.key.startsWith("marker:") ? sel.key.slice(7) : null;

function FeedbackBar({ sel, onFeedback }: { sel: Selection; onFeedback?: (itemId: string, verdict: Verdict) => Promise<void> }) {
  const [state, setState] = useState<{ key: string; msg: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const item = feedbackItemId(sel);
  const mine = state?.key === sel.key ? state : null;
  const send = async (v: Verdict) => {
    if (!item || !onFeedback) return;
    setBusy(true);
    try {
      await onFeedback(item, v);
      setState({ key: sel.key, ok: true, msg: `Recorded as ${v}. It applies to the next brief and to later incidents at this building.` });
    } catch (e) {
      setState({ key: sel.key, ok: false, msg: e instanceof Error ? e.message : "Could not record the feedback" });
    } finally { setBusy(false); }
  };
  const enabled = !!item && !!onFeedback && !busy;
  return (
    <div className="p-2.5">
      <div className="flex flex-wrap gap-1.5">
        <Btn onClick={enabled ? () => void send("confirmed") : undefined}>Confirm</Btn>
        <Btn onClick={enabled ? () => void send("wrong") : undefined}>Mark wrong</Btn>
        <Btn onClick={enabled ? () => void send("outdated") : undefined}>Mark outdated</Btn>
      </div>
      <div className={`mt-1.5 text-[12px] ${mine ? (mine.ok ? "text-verified" : "text-contradicted") : "text-faint"}`} role="status">
        {mine ? mine.msg : !onFeedback ? "Feedback needs a live incident." : !item ? "Feedback applies to facts, hazards and markers." : ""}
      </div>
    </div>
  );
}

export function Inspector({ sel, pool, onFeedback }: {
  sel: Selection | null;
  pool: Record<string, Evidence>;
  /** Live mode: record crew feedback for a fact key or marker id (POST /api/incidents/{id}/feedback). */
  onFeedback?: (itemId: string, verdict: Verdict) => Promise<void>;
}) {
  if (!sel) {
    return (
      <Panel title="Inspector">
        <div className="p-3 text-dim">Nothing selected.</div>
        <div className="px-3 text-[12px] text-faint">Select a fact, map marker or consideration to see its evidence.</div>
      </Panel>
    );
  }
  return (
    <Panel title="Inspector" right={sel.kind}>
      <div className="border-b border-line px-2.5 py-2">
        <div className="text-[14px] font-semibold leading-snug">{sel.title}</div>
        <div className="mt-1 flex items-center gap-3">
          {sel.status ? <StatusTag status={sel.status} /> : null}
          {sel.confidence !== undefined && (
            <div className="flex flex-1 items-center gap-2" title="Confidence">
              <div className="h-[3px] flex-1 bg-line"><div className="h-full bg-ink/70" style={{ width: `${sel.confidence * 100}%` }} /></div>
              <span className="num text-[11px] text-dim">{Math.round(sel.confidence * 100)}%</span>
            </div>
          )}
        </div>
      </div>
      {sel.fields?.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[88px_1fr] gap-2 border-b border-line px-2.5 py-1">
          <span className="label !normal-case !tracking-normal">{k}</span>
          <span className="num text-[12px]">{fmt(v)}</span>
        </div>
      ))}
      {sel.note && <div className="border-b border-line px-2.5 py-2 text-[12px] text-dim">{sel.note}</div>}
      <div className="label border-b border-line px-2.5 py-1.5">Evidence ({sel.evidence_ids.length})</div>
      {sel.evidence_ids.length === 0 ? (
        <div className="px-2.5 py-2 text-[12px] text-unverified">○ UNVERIFIED. No evidence on file.</div>
      ) : (
        <ul>
          {sel.evidence_ids.map((id) =>
            pool[id] ? (
              <EvidenceItem key={id} e={pool[id]} />
            ) : (
              <li key={id} className="num border-b border-line px-2.5 py-2 text-[12px] text-contradicted">▲ {id} does not resolve</li>
            ),
          )}
        </ul>
      )}
      <FeedbackBar sel={sel} onFeedback={onFeedback} />
    </Panel>
  );
}
