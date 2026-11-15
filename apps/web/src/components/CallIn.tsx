// Call in: one device records the caller's whole monologue (live/monologueRecorder.ts); Done sends it to the
// server, which transcribes it in one go (POST /api/calls/web/callin/audio). Every open instance of the web app
// follows the call through /ws/calls (live/callFeed.ts) and shows its status here; the transcript arrives on
// the incident's socket like any other.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { CallInfo } from "../live/callFeed";
import { MAX_SECONDS, micProblem, MonologueRecorder } from "../live/monologueRecorder";

export interface CallInCall {
  incident_id: string;
  call_label: string;
}

type Phase = "recording" | "sending" | "failed";

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function CallIn({ feed, onStart, canClear, onClear }: {
  feed: CallInfo | null;
  onStart: (c: CallInCall) => void;
  canClear: boolean;               // an incident is on screen
  onClear: () => void;             // back to "no active incident"
}) {
  const [mine, setMine] = useState<CallInCall | null>(null);   // this device is the caller
  const [phase, setPhase] = useState<Phase>("recording");
  const [secs, setSecs] = useState(0);
  const [level, setLevel] = useState(0);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const recRef = useRef<MonologueRecorder | null>(null);
  const wavRef = useRef<Blob | null>(null);    // kept until the server has it, for Retry
  const mineRef = useRef<CallInCall | null>(null);

  const reset = () => {
    recRef.current = null;
    wavRef.current = null;
    mineRef.current = null;
    setMine(null);
    setLevel(0);
    setSecs(0);
  };

  const send = async () => {
    const c = mineRef.current;
    const wav = wavRef.current;
    if (!c || !wav) return reset();
    setPhase("sending");
    setError(null);
    try {
      const q = new URLSearchParams({ incident_id: c.incident_id, call_label: c.call_label });
      const r = await fetch(`/api/calls/web/callin/audio?${q}`, { method: "POST", headers: { "content-type": "audio/wav" }, body: wav });
      if (r.status === 404) { setNote("The call had already ended."); return reset(); }
      if (!r.ok) throw new Error(r.status === 503 ? "Transcription failed." : `${r.status} ${await r.text()}`);
      const body = (await r.json()) as { recorded: number };
      setNote(body.recorded ? null : "No speech was heard in the recording.");
      reset();
    } catch (e) {
      setPhase("failed");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const done = () => {
    wavRef.current = recRef.current?.stop(true) ?? null;
    void send();
  };

  const start = async () => {
    const problem = micProblem();   // before anything is created: no half-open call on the server
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError(null);
    setNote(null);
    // The recorder's AudioContext is created inside the click (a user gesture).
    const rec = new MonologueRecorder((rms, s) => { setLevel(rms); setSecs(s); }, () => done());
    try {
      await rec.start();                                   // mic permission first, then the call
      const c: CallInCall = await api.callIn();
      recRef.current = rec;
      mineRef.current = c;
      setPhase("recording");
      setMine(c);
      onStart(c);
    } catch (e) {
      rec.stop(false);
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const cancel = () => {
    const c = mineRef.current;
    recRef.current?.stop(false);
    reset();
    if (c) api.endCall(c).catch(() => {});
  };

  // Clear call (any screen): cancel open calls everywhere, clear this screen; the next Call in is a new incident.
  const clear = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.clearCalls();
      setNote(null);
      onClear();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => () => { recRef.current?.stop(false); }, []);

  const btn = "shrink-0 border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider disabled:opacity-50";
  const meter = Math.min(1, level * 12);

  let body;
  if (mine && phase === "recording") {
    body = (
      <>
        <span className="num flex shrink-0 items-center gap-1.5 text-[11px] font-semibold text-contradicted">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-contradicted" aria-hidden />
          REC {clock(secs)}
        </span>
        <span className="h-1.5 w-16 shrink-0 bg-line" aria-hidden title="Mic level">
          <span className="block h-full bg-verified transition-[width] duration-100" style={{ width: `${meter * 100}%` }} />
        </span>
        <span className="hidden text-[11px] text-dim sm:inline">Tell us everything, then tap Done · max {MAX_SECONDS / 60} min</span>
        <button onClick={done} className={`${btn} border-verified/70 text-verified hover:bg-verified/15`}>Done</button>
        <button onClick={cancel} className={`${btn} border-line-hi text-dim hover:bg-accent/15`}>Cancel</button>
      </>
    );
  } else if (mine && phase === "sending") {
    body = <span className="num animate-pulse text-[11px] font-semibold text-accent">TRANSCRIBING YOUR CALL…</span>;
  } else if (mine && phase === "failed") {
    body = (
      <>
        <button onClick={() => void send()} className={`${btn} border-accent/70 text-accent hover:bg-accent/15`}>Retry</button>
        <button onClick={cancel} className={`${btn} border-line-hi text-dim hover:bg-accent/15`}>Discard</button>
      </>
    );
  } else if (feed) {
    body = (
      <span className="num flex items-center gap-1.5 text-[11px] font-semibold text-accent">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" aria-hidden />
        CALL IN PROGRESS · {feed.call_id.toUpperCase()} · {feed.status === "transcribing" ? "TRANSCRIBING…" : "CALLER SPEAKING"}
      </span>
    );
  } else {
    body = (
      <button onClick={() => void start()} disabled={busy}
        title="Start a call from this device. Every open FlameScout screen follows it; the whole call is transcribed when you tap Done."
        className={`${btn} border-line-hi text-ink hover:bg-accent/15`}>
        📞 {busy ? "Starting…" : "Call in"}
      </button>
    );
  }

  return (
    <div className="flex w-full min-w-0 flex-wrap items-center gap-2 md:w-auto md:flex-nowrap">
      {body}
      {!mine && (feed || canClear) && (
        <button onClick={() => void clear()} disabled={busy}
          title="End any open call on every screen and clear this one. The next Call in starts a new incident."
          className={`${btn} border-line-hi text-dim hover:bg-accent/15`}>
          ✕ Clear call
        </button>
      )}
      {(error || note) && (
        <span className={`min-w-0 text-[11px] md:max-w-[260px] md:truncate ${error ? "text-contradicted" : "text-dim"}`} title={error ?? note ?? ""}>
          {error ?? note}
        </span>
      )}
    </div>
  );
}
