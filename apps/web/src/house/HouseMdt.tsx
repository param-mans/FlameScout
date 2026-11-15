// MDT screen for a confirmed house that is in the viewer catalog with a stored plan (useConfirmedHouse state "house"),
// with or without the brief. Top: the address once and the 4 summary cards. Left: size-up (listing + plan, nothing
// invented) and up to 5 considerations. Center + right: one viewer iframe in layout=house (3D full height; the 2D plan
// and Surroundings stacked on the right). No inspector, legend, layers menu, version or live chip on this screen.
import { StatusTag, fmt } from "../components/ui";
import { BuildingViewer } from "../components/viewer/BuildingViewer";
import type { Brief } from "../types/schemas";
import { houseBrief, NA } from "./houseBrief";
import type { ConfirmedHouse } from "./liveFeed";
import { useHouseData } from "./useHouseData";

type Exposure = { side: string; distance_m: number; band?: string };
const HAZARDS = ["Gas", "Fireplace", "Garage", "Solar panels", "Pool"];   // listing mentions shown on this screen

function Readout({ label, value, sub, tone = "text-ink" }: { label: string; value: string; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="bg-panel px-3 py-2 text-left">
      <div className="label">{label}</div>
      <div className={`num text-[28px] font-semibold leading-tight ${tone}`}>{value}</div>
      <div className="h-4 text-[11px] text-dim">{sub}</div>
    </div>
  );
}

/** "9660 Fairdale Lane, Houston, Houston" -> "9660 Fairdale Lane, Houston" */
const dedupe = (a: string) => [...new Set(a.split(",").map((x) => x.trim()).filter(Boolean))].join(", ");

export function HouseMdt({ house, brief }: { house: Extract<ConfirmedHouse, { state: "house" }>; brief: Brief | null }) {
  const { sample, doc, incident, onPlanIndex, onIncidentState } = useHouseData(house.sampleId);
  const hb = sample ? houseBrief(sample, doc, incident.overlay) : null;
  const address = hb?.extras.header.address ?? dedupe(house.address);

  // size-up: listing first, plan where the listing has nothing; missing data stays "Not available"
  const plan = doc?.plan ?? null, L = sample?.listing ?? null;
  const floors = plan?.floors ?? [];
  const beds = floors.flatMap((f) => f.rooms).filter((r) => r.type === "Bedroom").length;
  const exits = (floors[0]?.openings ?? []).filter((o) => o.type === "doorway" && o.exterior === true).length;
  const stairs = plan?.stairs?.length ?? 0;
  const rows: [string, string][] = [
    ["Floors", plan ? String(floors.length) : doc?.listing_record?.stories != null ? String(doc.listing_record.stories) : NA],
    ["Year built", L?.yearBuilt != null ? String(L.yearBuilt) : NA],
    ["Area (sq ft)", L?.sqft ? Math.round(L.sqft).toLocaleString("en-US") : NA],
    ["Bedrooms", L?.beds != null ? String(L.beds) : plan ? String(beds) : NA],
    ["Exterior doors", plan ? String(exits) : NA],
    ["Stairs", plan ? (stairs ? String(stairs) : "Not detected") : NA],
  ];
  const hazards = (hb?.extras.hazards ?? []).map((h) => h.label).filter((h) => HAZARDS.includes(h));

  // considerations: the brief's (by rank), then the incident overlay's; titles only, 5 at most
  const cons = [
    ...[...(brief?.considerations ?? [])].sort((a, b) => a.rank - b.rank).map((c) => ({ id: `b-${c.id}`, text: c.title })),
    ...(hb?.extras.considerations ?? []).filter((c) => c.source === "incident").map((c) => ({ id: `i-${c.id}`, text: c.text })),
  ].slice(0, 5);

  // summary cards: same as the brief screen, without version or scan text; before the brief, "—"
  const floor = brief?.size_up["incident.floor"];
  const stairMarkers = brief?.markers.filter((m) => m.kind === "stair").length ?? 0;
  const stairwells = brief && stairMarkers ? stairMarkers : plan ? stairs : null;
  const exposures = (brief?.exposures ?? []) as Exposure[];
  const closest = exposures.length ? exposures.reduce((a, b) => (a.distance_m <= b.distance_m ? a : b)) : null;
  const hazardCount = brief ? brief.hazards.length : hb ? hb.brief.hazards.length : null;

  return (
    <div className="flex h-full flex-col" data-confirmed-building={house.buildingId} data-house-mdt>
      <header className="flex h-8 shrink-0 items-center border-b border-line bg-panel px-3 text-[13px] font-semibold uppercase">{address}</header>

      <div className="grid shrink-0 grid-cols-4 gap-px border-b border-line bg-line">
        <Readout label="Reported floor" value={fmt(floor?.value)} tone={floor?.status === "contradicted" ? "text-contradicted" : "text-ink"}
          sub={floor && <StatusTag status={floor.status} confidence={floor.confidence} />} />
        <Readout label="Stairwells identified" value={stairwells == null ? "—" : String(stairwells)} sub={stairwells != null && stairwells < 2 ? "verify on arrival" : undefined} />
        <Readout label="Hazard flags" value={hazardCount == null ? "—" : String(hazardCount)} sub={brief ? "from tenants (inferred)" : hb ? "from listing" : undefined} />
        <Readout label="Closest exposure" value={closest ? `${closest.distance_m} m` : "—"} tone={closest?.band === "close" ? "text-reported" : "text-ink"}
          sub={closest && [closest.side, closest.band].filter(Boolean).join(" · ")} />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[224px_minmax(0,1fr)] gap-px bg-line max-[1100px]:grid-cols-[210px_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto bg-panel">
          <div className="label border-b border-line bg-bg px-3 py-1.5">Size-up</div>
          {rows.map(([k, v]) => (
            <div key={k} data-sizeup-row className="flex items-baseline justify-between gap-2 border-b border-line px-3 py-2">
              <span data-k className="text-[16px] text-dim">{k}</span>
              <span data-v className={`num text-right text-[20px] font-bold ${v === NA ? "text-faint" : "text-ink"}`}>{v}</span>
            </div>
          ))}
          {hazards.length > 0 && (
            <div data-hazards className="border-b border-line px-3 py-2 text-[16px]">
              <span className="text-dim">Hazards </span><span className="font-semibold text-reported">{hazards.join(" · ")}</span>
            </div>
          )}
          {cons.length > 0 && (
            <>
              <div className="label border-b border-line bg-bg px-3 py-1.5">Considerations</div>
              {cons.map((c) => (
                <div key={c.id} className="border-b border-line px-3 py-2 text-[14px] leading-snug text-ink"><span className="line-clamp-3">{c.text}</span></div>
              ))}
            </>
          )}
        </aside>
        <section className="relative min-h-0 bg-bg">
          <BuildingViewer layout="house" sampleId={house.sampleId} onIncidentState={onIncidentState} onPlanIndex={onPlanIndex} />
        </section>
      </div>
    </div>
  );
}
