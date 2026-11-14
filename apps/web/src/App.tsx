import { useState } from "react";
import { DispatcherConsole } from "./components/DispatcherConsole";
import { MdtView } from "./components/MdtView";
import { Btn } from "./components/ui";
import { briefNoScan, briefV1, briefV2, candidates, incident, segments } from "./mock/s2";

type View = "dispatcher" | "mdt";
const BRIEFS = { v1: briefV1, "v2 diff": briefV2, "no scan": briefNoScan } as const;
type BriefKey = keyof typeof BRIEFS;

export default function App() {
  const [view, setView] = useState<View>("mdt");
  const [briefKey, setBriefKey] = useState<BriefKey>("v2 diff");
  const [confirmedId, setConfirmedId] = useState<string | null>(null);

  return (
    <div className="flex h-full flex-col">
      <nav className="flex h-9 shrink-0 items-center gap-1 border-b border-line bg-panel px-3">
        <span className="mr-4 flex items-center gap-2 text-[12px] font-bold tracking-[0.18em]">
          <span className="inline-block h-2.5 w-2.5 bg-accent" aria-hidden />
          FLAMESCOUT
        </span>
        <Btn active={view === "dispatcher"} onClick={() => setView("dispatcher")}>Dispatcher</Btn>
        <Btn active={view === "mdt"} onClick={() => setView("mdt")}>MDT</Btn>
        {/* Stand-in for live WebSocket data. TODO: replace with the admin fixture toggle (PRD 10). */}
        <div className="ml-auto flex items-center gap-1">
          <span className="label mr-1">Sim data</span>
          {(Object.keys(BRIEFS) as BriefKey[]).map((k) => (
            <Btn key={k} active={briefKey === k} onClick={() => setBriefKey(k)}>{k}</Btn>
          ))}
          <span className="num ml-2 border border-reported/60 px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-reported">SIMULATION</span>
        </div>
      </nav>
      <main className="min-h-0 flex-1">
        {view === "dispatcher" ? (
          <DispatcherConsole segments={segments} incident={incident} candidates={candidates} confirmedId={confirmedId} onConfirm={setConfirmedId} />
        ) : (
          <MdtView key={briefKey} brief={BRIEFS[briefKey]} segments={segments} contradictions={incident.contradictions} />
        )}
      </main>
    </div>
  );
}
