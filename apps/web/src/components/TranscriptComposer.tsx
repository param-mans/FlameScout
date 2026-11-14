import { useState } from "react";
import { factLabel } from "../labels";
import type { CallSegment, ExtractedFact } from "../types/schemas";
import { Btn, fmt } from "./ui";

/** What the extractor understood from one caller line. Empty = nothing was extracted, which is worth seeing. */
export function ExtractedChips({ facts }: { facts: ExtractedFact[] }) {
  if (facts.length === 0) {
    return <div className="num mt-0.5 text-[11px] text-faint">○ no facts extracted from this line</div>;
  }
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {facts.map((f, i) => (
        <span key={i} className="num border border-line-hi px-1.5 py-0.5 text-[11px] text-dim" title={`${f.key} · quote: “${f.quote}”`}>
          {factLabel(f.key)}: <span className="text-ink">{fmt(f.value)}</span>
        </span>
      ))}
    </div>
  );
}

/** Type a transcript line into the incident: as the caller or the call-taker, on an existing or a new call. */
export function TranscriptComposer({ callIds, onSend }: {
  callIds: string[];
  onSend: (text: string, speaker: CallSegment["speaker"], callId: string) => void;
}) {
  const [text, setText] = useState("");
  const [speaker, setSpeaker] = useState<CallSegment["speaker"]>("caller");
  const newCall = `c${callIds.length + 1}`;
  const [pick, setPick] = useState<string | null>(null);   // null = the latest call
  const callId = pick ?? callIds[callIds.length - 1] ?? "c1";

  const send = () => {
    if (!text.trim()) return;
    onSend(text, speaker, callId);
    setText("");
    setPick(null);
  };

  return (
    <div className="border-t border-line bg-panel p-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <Btn active={speaker === "caller"} onClick={() => setSpeaker("caller")}>Caller</Btn>
        <Btn active={speaker === "call_taker"} onClick={() => setSpeaker("call_taker")}>Call-taker</Btn>
        <select
          value={callId}
          onChange={(e) => setPick(e.target.value)}
          aria-label="Call"
          className="num border border-line-hi bg-bg px-1.5 py-0.5 text-[11px] text-ink"
        >
          {callIds.map((c) => <option key={c} value={c}>{c.toUpperCase()}</option>)}
          {!callIds.includes(newCall) && <option value={newCall}>{newCall.toUpperCase()} (new call)</option>}
        </select>
        <span className="num ml-auto text-[11px] text-faint">
          {speaker === "caller" ? "facts are read from caller lines" : "call-taker lines are not read for facts"}
        </span>
      </div>
      <div className="flex gap-1.5">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          rows={2}
          placeholder="Type what the caller says, e.g. “There's propane tanks in the garage…”  Enter to send"
          aria-label="Transcript line"
          className="num min-w-0 flex-1 resize-none border border-line-hi bg-bg px-2 py-1 text-[13px] text-ink outline-none placeholder:text-faint focus:border-accent"
        />
        <button
          onClick={send}
          disabled={!text.trim()}
          className="border border-accent/70 px-3 text-[12px] font-semibold uppercase tracking-wider text-ink hover:bg-accent/15 disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </div>
  );
}
