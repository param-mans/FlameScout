import { Btn } from "./ui";
import { SamplePicker, type SampleHouse } from "./viewer/BuildingViewer";

// Demo scaffolding, kept out of the MDT: simulated brief data and the temporary sample-house picker.
// The house picked here is the incident building the MDT viewer loads.
export function DemoControls<K extends string>({
  briefKeys, briefKey, onBriefKey, samples, samplesError, sampleId, onSample,
}: {
  briefKeys: readonly K[];
  briefKey: K;
  onBriefKey: (k: K) => void;
  samples: SampleHouse[] | null;
  samplesError: string | null;
  sampleId: string | null;
  onSample: (id: string) => void;
}) {
  return (
    <details className="group shrink-0 border-b border-line bg-panel">
      <summary className="label flex h-7 cursor-pointer list-none items-center gap-2 px-3 hover:text-ink">
        <span aria-hidden className="inline-block transition-transform group-open:rotate-90">▸</span>
        Demo / Controls
        <span className="num ml-auto border border-reported/60 px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-reported">SIMULATION</span>
      </summary>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-line px-3 py-2">
        <div className="flex items-center gap-1">
          {/* Stand-in for live WebSocket data. TODO: replace with the admin fixture toggle (PRD 10). */}
          <span className="label mr-1">Sim data</span>
          {briefKeys.map((k) => (
            <Btn key={k} active={briefKey === k} onClick={() => onBriefKey(k)}>{k}</Btn>
          ))}
        </div>
        {samples ? (
          <SamplePicker samples={samples} value={sampleId} onChange={onSample} />
        ) : (
          <span className="label">{samplesError ?? "Loading sample houses…"}</span>
        )}
      </div>
    </details>
  );
}
