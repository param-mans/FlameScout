import { useState } from "react";
import { DemoControls } from "./components/DemoControls";
import { DispatcherConsole } from "./components/DispatcherConsole";
import { MdtView } from "./components/MdtView";
import { Btn } from "./components/ui";
import { readSampleParam, setSampleParam, useSampleHouses } from "./components/viewer/BuildingViewer";
import { briefNoScan, briefV1, briefV2, candidates, incident, segments } from "./mock/s2";

type View = "dispatcher" | "mdt";
const BRIEFS = { v1: briefV1, "v2 diff": briefV2, "no scan": briefNoScan } as const;
type BriefKey = keyof typeof BRIEFS;
const BRIEF_KEYS = Object.keys(BRIEFS) as BriefKey[];

export default function App() {
  const [view, setView] = useState<View>("mdt");
  const [briefKey, setBriefKey] = useState<BriefKey>("v2 diff");
  const [confirmedId, setConfirmedId] = useState<string | null>(null);

  // Incident building for the MDT viewer: the sample picked in Demo / Controls (?sample=<id>), else the first sample.
  const { samples, error: samplesError } = useSampleHouses();
  const [pickedSample, setPickedSample] = useState<string | null>(readSampleParam);
  const sampleId = samples ? (samples.some((s) => s.id === pickedSample) ? pickedSample : samples[0]?.id ?? null) : null;
  const pickSample = (id: string) => { setPickedSample(id); setSampleParam(id); };

  return (
    <div className="flex h-full flex-col">
      <nav className="flex h-9 shrink-0 items-center gap-1 border-b border-line bg-panel px-3">
        <span className="mr-4 flex items-center gap-2 text-[12px] font-bold tracking-[0.18em]">
          <span className="inline-block h-2.5 w-2.5 bg-accent" aria-hidden />
          FLAMESCOUT
        </span>
        <Btn active={view === "dispatcher"} onClick={() => setView("dispatcher")}>Dispatcher</Btn>
        <Btn active={view === "mdt"} onClick={() => setView("mdt")}>MDT</Btn>
      </nav>
      <main className="min-h-0 flex-1">
        {view === "dispatcher" ? (
          <DispatcherConsole
            segments={segments} incident={incident} candidates={candidates} confirmedId={confirmedId} onConfirm={setConfirmedId}
            demo={
              <DemoControls briefKeys={BRIEF_KEYS} briefKey={briefKey} onBriefKey={setBriefKey}
                samples={samples} samplesError={samplesError} sampleId={sampleId} onSample={pickSample} />
            }
          />
        ) : (
          <MdtView key={briefKey} brief={BRIEFS[briefKey]} sampleId={sampleId} sampleError={samplesError} />
        )}
      </main>
    </div>
  );
}
