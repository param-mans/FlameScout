// MDT before the first brief, once the live incident has a confirmed building: the floor plan + 3D of that house
// right away (no overlay, no brief markers needed), or "No interior scan on file" when the building is not in the
// viewer catalog or has no stored plan. The orchestrator's plan stays on the side until the brief arrives.
import { BuildingViewer } from "../components/viewer/BuildingViewer";
import { PlanList } from "../components/PlanList";
import type { Progress } from "../types/schemas";
import type { ConfirmedHouse } from "./liveFeed";

export function NoScanOnFile() {
  return (
    <div className="grid h-full place-items-center text-center" data-no-scan>
      <div>
        <div className="num text-[15px] text-unverified">○ NO INTERIOR SCAN ON FILE</div>
        <div className="mt-1 text-[12px] text-faint">Interior sections are gaps, not guesses.</div>
      </div>
    </div>
  );
}

export function ConfirmedHouseView({ house, info, progress, waiting }: {
  house: Exclude<ConfirmedHouse, { state: "none" }>;
  info: { name: string; address: string };
  progress?: Progress | null;
  waiting?: string;
}) {
  const address = house.state === "house" || house.state === "noScan" ? house.address : null;
  return (
    <div className="flex h-full flex-col" data-confirmed-building={house.buildingId}>
      <header className="flex h-8 items-center border-b border-line bg-panel">
        <div className="px-3 text-[13px] font-semibold uppercase">{info.name !== "No building confirmed" ? info.name : address ?? house.buildingId}</div>
        <div className="num text-[12px] text-dim">{info.address || (address ?? "")}</div>
        <div className="num border-l border-line px-3 text-[11px] font-semibold leading-8 text-verified">● LOCATION CONFIRMED</div>
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-[1fr_300px] gap-px bg-line">
        <section className="relative min-h-0 bg-bg">
          {house.state === "house" ? <BuildingViewer layout="split" sampleId={house.sampleId} />
            : house.state === "noScan" ? <NoScanOnFile />
            : <div className="num grid h-full place-items-center text-[13px] text-dim">LOADING BUILDING…</div>}
        </section>
        <aside className="min-h-0 overflow-y-auto bg-panel p-3">
          <div className="num text-[13px] text-dim">{waiting ?? "BUILDING THE BRIEF…"}</div>
          <div className="mt-1 text-[12px] text-faint">Size-up, hazards and considerations appear when the brief is ready.</div>
          <div className="mt-3"><PlanList progress={progress ?? null} /></div>
        </aside>
      </div>
    </div>
  );
}
