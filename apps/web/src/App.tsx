import { useMemo, useState } from "react";
import { useLiveIncident } from "./api/useLiveIncident";
import { useCallFeed } from "./live/callFeed";
import { DemoControls } from "./components/DemoControls";
import { DispatcherConsole } from "./components/DispatcherConsole";
import { MdtView, type IncidentInfo } from "./components/MdtView";
import { CallIn } from "./components/CallIn";
import { Btn } from "./components/ui";
import { humanizeKeys } from "./labels";
import { readSampleParam, setSampleParam, useSampleHouses } from "./components/viewer/BuildingViewer";
import { briefNoScan, briefV1, briefV2, candidates as mockCandidates, incident as mockIncident, segments as mockSegments } from "./mock/s2";
import type { IncidentRecord } from "./types/schemas";

type View = "dispatcher" | "mdt";
const BRIEFS = { v1: briefV1, "v2 diff": briefV2, "no scan": briefNoScan } as const;
type BriefKey = keyof typeof BRIEFS;
const BRIEF_KEYS = Object.keys(BRIEFS) as BriefKey[];

// ?mock=1 never talks to the API. Otherwise the app uses the API when /health answers and falls back to mock data.
const FORCE_MOCK = new URLSearchParams(window.location.search).get("mock") === "1";
// ?admin=1 shows the demo scaffolding (mock brief toggle, sample-house picker). Hidden by default.
const ADMIN = new URLSearchParams(window.location.search).get("admin") === "1";

const EMPTY_INCIDENT: IncidentRecord = {
  incident_id: "", status: "listening", call_ids: [], facts: {}, evidence: {}, contradictions: [], open_questions: [],
  confirmed_building_id: null, ready_to_brief: false,
};

export default function App() {
  const [view, setView] = useState<View>("mdt");
  const [briefKey, setBriefKey] = useState<BriefKey>("v2 diff");
  const [mockConfirmed, setMockConfirmed] = useState<string | null>(null);
  const [script, setScript] = useState("S2");
  const live = useLiveIncident(FORCE_MOCK);
  const st = live.state;
  const isLive = st.mode === "live";
  // Call in on any device: every open instance follows that call's incident in the Dispatcher view.
  const activeCall = useCallFeed((c) => {
    if (FORCE_MOCK) return;
    void live.attach(c.incident_id);
    setView("dispatcher");
  }, () => { if (!FORCE_MOCK) live.reset(); });

  // Incident building for the MDT viewer: the sample picked in Demo / Controls (?sample=<id>), else the first sample.
  // TODO: map the confirmed API building to a sample house once the heroes have scans in the viewer.
  const { samples, error: samplesError } = useSampleHouses();
  const [pickedSample, setPickedSample] = useState<string | null>(readSampleParam);
  const sampleId = samples ? (samples.some((s) => s.id === pickedSample) ? pickedSample : samples[0]?.id ?? null) : null;
  const pickSample = (id: string) => { setPickedSample(id); setSampleParam(id); };

  // One shape for both sources.
  const incident = isLive ? st.incident ?? EMPTY_INCIDENT : mockIncident;
  const segments = isLive ? st.segments : mockSegments;
  const candidates = isLive ? st.candidates : mockCandidates;
  const confirmedId = isLive ? incident.confirmed_building_id ?? null : mockConfirmed;
  const brief = isLive ? st.brief : BRIEFS[briefKey];
  const questions = isLive ? st.suggestions.map((x) => humanizeKeys(x.question)) : undefined;

  const info = useMemo<IncidentInfo>(() => {
    const c = candidates.find((x) => x.building_id === confirmedId) ?? (isLive ? undefined : candidates[0]);
    // Reported facts only: the extractor's "incident.visible" ("smoke" / "flames"), or the mock's "smoke.visible".
    const seen = incident.facts["incident.visible"]?.value ?? (incident.facts["smoke.visible"]?.value === true ? "smoke" : null);
    return {
      name: c ? c.name ?? c.address : "No building confirmed",
      address: c?.address ?? "",
      label: seen ? `Fire incident · ${String(seen)} reported` : "Fire incident",
    };
  }, [candidates, confirmedId, incident.facts, isLive]);

  const waiting = st.busy ?? (candidates.length === 0 ? (st.incidentId ? "LISTENING…" : "NO ACTIVE INCIDENT") : undefined);
  const scriptIds = st.scripts.map((x) => x.script_id);
  const scriptValue = script === "" || scriptIds.includes(script) ? script : scriptIds[0] ?? script;   // "" = blank incident

  return (
    <div className="flex h-full flex-col">
      {/* Phones and iPad portrait: tabs + status on the first row, call controls wrap below. lg+: one row. */}
      <nav className="flex min-h-9 shrink-0 flex-wrap items-center gap-x-1 gap-y-1.5 border-b border-line bg-panel px-3 py-1">
        <span className="mr-2 flex items-center gap-2 text-[12px] font-bold tracking-[0.18em] sm:mr-4">
          <span className="inline-block h-2.5 w-2.5 bg-accent" aria-hidden />
          FLAMESCOUT
        </span>
        <Btn active={view === "dispatcher"} onClick={() => setView("dispatcher")}>Dispatcher</Btn>
        <Btn active={view === "mdt"} onClick={() => setView("mdt")}>MDT</Btn>

        <div className="ml-auto flex items-center gap-2 lg:order-last lg:ml-0">
          {st.mode === "mock" && <Btn onClick={live.retryApi}>Retry API</Btn>}
          <span className="num hidden text-[10px] font-semibold tracking-wider text-dim sm:inline" title="Simulation only: no real emergency lines are called">SIMULATION</span>
          <span
            className={`num border px-1.5 py-0.5 text-[10px] font-semibold tracking-wider ${
              isLive ? (st.conn === "open" || !st.incidentId ? "border-verified/60 text-verified" : "border-reported/60 text-reported")
              : "border-reported/60 text-reported"
            }`}
            title={isLive ? `API connected${st.incidentId ? ` · socket ${st.conn}` : ""}` : st.mode === "checking" ? "Checking the API…" : "API not reachable: showing mock data"}
          >
            {isLive ? (st.incidentId && st.conn !== "open" ? `● LIVE · ${st.conn.toUpperCase()}` : "● LIVE") : st.mode === "checking" ? "CHECKING API…" : "MOCK DATA"}
          </span>
        </div>

        {isLive && (
          <div className="flex w-full min-w-0 flex-wrap items-center gap-2 lg:ml-auto lg:w-auto lg:flex-nowrap">
            <select
              value={scriptValue}
              onChange={(e) => setScript(e.target.value)}
              aria-label="Scripted call"
              className="num border border-line-hi bg-bg px-1.5 py-0.5 text-[11px] text-ink"
            >
              {st.scripts.map((x) => <option key={x.script_id} value={x.script_id}>{x.script_id} · {x.segments} segments</option>)}
              <option value="">Blank · type your own</option>
            </select>
            <Btn onClick={() => void live.startCall(scriptValue)}>
              {scriptValue === "" ? (st.incidentId ? "New blank incident" : "Start blank incident") : st.incidentId ? "New call" : "Run call"}
            </Btn>
            {st.incidentId && <span className="num hidden text-[11px] text-faint sm:inline" title="Incident id">{st.incidentId}</span>}
            <CallIn feed={activeCall} onStart={(c) => { void live.attach(c.incident_id); setView("dispatcher"); }}
              canClear={!!st.incidentId} onClear={live.reset} />
          </div>
        )}
      </nav>

      {st.error && (
        <div className="num border-b border-contradicted/50 bg-contradicted/10 px-3 py-1 text-[12px] text-contradicted" role="alert">▲ {st.error}</div>
      )}

      <main className="min-h-0 flex-1">
        {view === "dispatcher" ? (
          <DispatcherConsole
            segments={segments} incident={incident} candidates={candidates} confirmedId={confirmedId}
            onConfirm={isLive ? (id) => void live.confirm(id) : setMockConfirmed}
            onResolve={isLive ? (k, v) => void live.resolve(k, v) : undefined}
            questions={questions}
            onSend={isLive ? (text, speaker, callId) => void live.sendLine(text, speaker, callId) : undefined}
            extracted={isLive ? st.extracted : undefined}
            progress={isLive ? st.progress : null}
            busy={isLive ? st.busy : null}
            held={isLive ? st.held : null}
            onApproveHeld={isLive ? () => void live.approveHeld() : undefined}
            demo={ADMIN ? (
              <DemoControls briefKeys={isLive ? [] : BRIEF_KEYS} briefKey={briefKey} onBriefKey={setBriefKey}
                samples={samples} samplesError={samplesError} sampleId={sampleId} onSample={pickSample} />
            ) : undefined}
          />
        ) : (
          <MdtView key={isLive ? st.incidentId ?? "none" : briefKey} brief={brief} info={info} sampleId={sampleId} sampleError={samplesError}
            progress={isLive ? st.progress : null} waiting={isLive ? waiting : undefined} />
        )}
      </main>
    </div>
  );
}
